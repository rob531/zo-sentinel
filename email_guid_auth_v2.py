import os
import re
import json
import time
import hashlib
import secrets
import logging
import signal
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
import uvicorn

SERVICE_NAME = "email_guid_auth"
SERVICE_PORT = 8775
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
WRITE_URL = "http://127.0.0.1:8772/write"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

app = FastAPI()

_tokens: dict[str, dict] = {}
_pending: dict[str, dict] = {}


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    raise SystemExit(0)


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = f.read().strip()
        if old_pid and os.path.exists(f"/proc/{old_pid}"):
            logger.error(f"Another instance already running with PID {old_pid}")
            raise SystemExit(1)
        else:
            remove_pid_file()
    pid = os.getpid()
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def ws_query(sql: str) -> list[dict]:
    try:
        import requests
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list[dict]):
    try:
        import requests
        resp = requests.post(WRITE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return {}


def ws_execute(sql: str):
    try:
        import requests
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_email_guid(email: str, purpose: str = "auth") -> dict:
    now = utc_now_iso()
    raw_token = f"{email}:{purpose}:{secrets.token_hex(16)}:{now}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()[:32]
    guid = f"guid_{token_hash}"
    expires_at = datetime.now(timezone.utc)
    delta_map = {"auth": 15, "recovery": 60, "verify": 1440}
    minutes = delta_map.get(purpose, 60)
    expires_at = (expires_at.replace(minute=expires_at.minute + minutes) if hasattr(expires_at, 'replace') else
                 expires_at + timedelta(minutes=minutes) if 'timedelta' in dir() else
                 (datetime.now(timezone.utc) + timedelta(minutes=minutes) if 'timedelta' in dir() else
                  expires_at))
    try:
        from datetime import timedelta as _td
        expires_at = datetime.now(timezone.utc) + _td(minutes=minutes)
    except Exception:
        expires_at = datetime.now(timezone.utc)
    expires_iso = (expires_at + timedelta(minutes=minutes)) if False else datetime.now(timezone.utc)
    try:
        from datetime import timedelta as _td
        expires_iso = datetime.now(timezone.utc) + _td(minutes=minutes)
    except Exception:
        expires_iso = expires_at

    return {
        "guid": guid,
        "email": email,
        "purpose": purpose,
        "token": token_hash,
        "created_at": now,
        "expires_at": expires_iso.isoformat(),
        "used": False
    }


def validate_guid(guid: str, token: str, email: str) -> dict:
    now = datetime.now(timezone.utc)
    row = ws_query(
        f"SELECT * FROM auth_tokens WHERE token_id = '{guid}' AND admin_email = '{email}' LIMIT 1"
    )
    if not row:
        return {"valid": False, "reason": "GUID_NOT_FOUND"}

    record = row[0]
    if record.get("used"):
        return {"valid": False, "reason": "ALREADY_USED"}
    if record.get("expires_at"):
        exp_str = record["expires_at"]
        try:
            exp_ts = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            if exp_ts < now:
                return {"valid": False, "reason": "EXPIRED"}
        except Exception:
            pass
    if record.get("token") != token:
        return {"valid": False, "reason": "TOKEN_MISMATCH"}

    return {"valid": True, "reason": "OK"}


def ensure_tables():
    ws_execute("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token_id VARCHAR,
            action VARCHAR,
            mcp_name VARCHAR,
            submission_id VARCHAR,
            admin_email VARCHAR,
            expires_at TIMESTAMPTZ,
            used BOOLEAN DEFAULT FALSE,
            used_at TIMESTAMPTZ,
            PRIMARY KEY (token_id)
        )
    """)


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "port": SERVICE_PORT}


@app.post("/guid/generate")
def api_generate_guid(payload: dict, authorization: Optional[str] = Header(None)):
    email = payload.get("email")
    purpose = payload.get("purpose", "auth")
    if not email:
        raise HTTPException(status_code=400, detail="email required")

    record = generate_email_guid(email, purpose)
    ws_write("auth_tokens", [{
        "token_id": record["guid"],
        "action": purpose,
        "mcp_name": payload.get("mcp_name", ""),
        "submission_id": payload.get("submission_id", ""),
        "admin_email": email,
        "expires_at": record["expires_at"],
        "used": False
    }])
    return {"guid": record["guid"], "expires_at": record["expires_at"]}


@app.post("/guid/validate")
def api_validate_guid(payload: dict, authorization: Optional[str] = Header(None)):
    guid = payload.get("guid")
    token = payload.get("token")
    email = payload.get("email")
    if not all([guid, token, email]):
        raise HTTPException(status_code=400, detail="guid, token, email required")

    result = validate_guid(guid, token, email)
    if result["valid"]:
        ws_execute(f"UPDATE auth_tokens SET used = TRUE, used_at = '{utc_now_iso()}' WHERE token_id = '{guid}'")
    return result


@app.get("/guid/status/{guid}")
def api_guid_status(guid: str, authorization: Optional[str] = Header(None)):
    rows = ws_query(f"SELECT * FROM auth_tokens WHERE token_id = '{guid}' LIMIT 1")
    if not rows:
        raise HTTPException(status_code=404, detail="GUID_NOT_FOUND")
    return rows[0]


def run():
    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    ensure_tables()
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)


if __name__ == "__main__":
    run()