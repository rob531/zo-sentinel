import os
import sys
import time
import json
import uuid
import hashlib
import hmac
import base64
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import JSONResponse
import uvicorn
import requests

# deps: fastapi,uvicorn,requests,python-jose,cryptography

SERVICE_NAME = "email_guid_auth"
SERVICE_PORT = 8775
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 30
TOKEN_EXPIRY_HOURS = 24

JWT_SECRET = os.environ.get("JWT_SECRET", "sentinel-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"

app = FastAPI()

start_time = time.time()


def log(msg: str) -> None:
    print(f"[{datetime.utcnow().isoformat()}] {SERVICE_NAME}: {msg}", flush=True)


def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Query error: {e}")
        return {"rows": [], "count": 0}


def ws_write(table: str, rows: list) -> Dict[str, Any]:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Write error: {e}")
        return {"ok": False}


def ws_execute(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Execute error: {e}")
        return {"ok": False}


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Already running as PID {old_pid}")
            return False
        except OSError:
            log("Stale PID file, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass


def signal_handler(signum, frame):
    log(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def ensure_auth_tokens_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS auth_tokens (
        token_id VARCHAR PRIMARY KEY,
        action VARCHAR NOT NULL,
        mcp_name VARCHAR,
        submission_id VARCHAR,
        admin_email VARCHAR NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used BOOLEAN DEFAULT FALSE,
        used_at TIMESTAMP
    )
    """
    ws_execute(sql)


def generate_token_id() -> str:
    return str(uuid.uuid4())


def create_auth_token(action: str, admin_email: str, mcp_name: Optional[str] = None,
                     submission_id: Optional[str] = None) -> str:
    token_id = generate_token_id()
    expires_at = (datetime.utcnow() + timedelta(hours=TOKEN_EXPIRY_HOURS)).isoformat()
    
    ws_write("auth_tokens", [{
        "token_id": token_id,
        "action": action,
        "mcp_name": mcp_name,
        "submission_id": submission_id,
        "admin_email": admin_email,
        "expires_at": expires_at,
        "used": False
    }])
    
    return token_id


def verify_token(token_id: str) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT token_id, action, mcp_name, submission_id, admin_email, expires_at, used, used_at
    FROM auth_tokens
    WHERE token_id = '{token_id}'
    """
    result = ws_query(sql)
    if not result.get("rows"):
        return None
    
    token_data = result["rows"][0]
    expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))
    
    if datetime.utcnow() > expires_at:
        log(f"Token expired: {token_id}")
        return None
    
    if token_data["used"]:
        log(f"Token already used: {token_id}")
        return None
    
    return token_data


def mark_token_used(token_id: str) -> None:
    used_at = datetime.utcnow().isoformat()
    sql = f"UPDATE auth_tokens SET used = TRUE, used_at = '{used_at}' WHERE token_id = '{token_id}'"
    ws_execute(sql)


def generate_email_link(token_id: str, base_url: str = "http://127.0.0.1:8790") -> str:
    return f"{base_url}/auth/verify?token={token_id}"


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "uptime": int(time.time() - start_time)}


@app.get("/generate")
def generate(
    action: str = Query(...),
    email: str = Query(...),
    mcp_name: Optional[str] = Query(None),
    submission_id: Optional[str] = Query(None)
):
    if action not in ["approve", "reject", "submit", "override"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    token_id = create_auth_token(action, email, mcp_name, submission_id)
    auth_link = generate_email_link(token_id)
    
    log(f"Generated token for {email}: {token_id} (action={action})")
    
    return {
        "token_id": token_id,
        "auth_link": auth_link,
        "expires_in_hours": TOKEN_EXPIRY_HOURS
    }


@app.get("/verify")
def verify(token: str = Query(...)):
    token_data = verify_token(token)
    
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    mark_token_used(token)
    
    log(f"Token verified: {token} (action={token_data['action']})")
    
    return {
        "valid": True,
        "action": token_data["action"],
        "admin_email": token_data["admin_email"],
        "mcp_name": token_data.get("mcp_name"),
        "submission_id": token_data.get("submission_id"),
        "verified_at": datetime.utcnow().isoformat()
    }


@app.get("/status/{token_id}")
def status(token_id: str):
    sql = f"""
    SELECT token_id, action, admin_email, expires_at, used, used_at
    FROM auth_tokens
    WHERE token_id = '{token_id}'
    """
    result = ws_query(sql)
    
    if not result.get("rows"):
        return {"found": False}
    
    token_data = result["rows"][0]
    expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))
    is_expired = datetime.utcnow() > expires_at
    
    return {
        "found": True,
        "token_id": token_id,
        "action": token_data["action"],
        "admin_email": token_data["admin_email"],
        "used": token_data["used"],
        "used_at": token_data.get("used_at"),
        "expires_at": token_data["expires_at"],
        "expired": is_expired,
        "valid": not token_data["used"] and not is_expired
    }


@app.get("/audit")
def audit(
    admin_email: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, le=200)
):
    conditions = []
    if admin_email:
        conditions.append(f"admin_email = '{admin_email}'")
    if action:
        conditions.append(f"action = '{action}'")
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
    SELECT token_id, action, mcp_name, submission_id, admin_email, expires_at, used, used_at
    FROM auth_tokens
    WHERE {where_clause}
    ORDER BY expires_at DESC
    LIMIT {limit}
    """
    result = ws_query(sql)
    
    return {
        "count": result.get("count", 0),
        "tokens": result.get("rows", [])
    }


@app.post("/invalidate")
def invalidate(token_id: str):
    sql = f"DELETE FROM auth_tokens WHERE token_id = '{token_id}'"
    ws_execute(sql)
    log(f"Token invalidated: {token_id}")
    return {"ok": True, "token_id": token_id}


def send_heartbeat():
    try:
        requests.post(WRITE_SERVICE_URL, json={
            "table": "service_health",
            "rows": [{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}],
            "wait": True
        }, timeout=5)
    except Exception as e:
        log(f"Heartbeat error: {e}")


def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        sys.exit(1)
    
    log(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    ensure_auth_tokens_table()
    send_heartbeat()
    
    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()