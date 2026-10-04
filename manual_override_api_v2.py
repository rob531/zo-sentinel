import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "manual_override_api_v2.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

SERVICE_NAME = "manual_override_api_v2"
SERVICE_PORT = 8776
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

app = FastAPI(title="Manual Override API v2")

HTTP_TIMEOUT = 15.0


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed: %s | table=%s", e, table)
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error("Already running as PID %s. Exiting.", old_pid)
            sys.exit(1)
        except OSError:
            log.warning("Stale PID file %s (pid %s not alive). Removing.", PID_FILE, old_pid)
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.warning("Failed to remove PID file: %s", e)


def signal_handler(signum, frame) -> None:
    sig_name = signal.Signals(signum).name
    log.info("Received %s, shutting down gracefully.", sig_name)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat() -> None:
    ts = utc_now_iso()
    rows = [{
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "running",
        "meta": "{}"
    }]
    ws_write("service_health", rows)


def ensure_tables() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS override_metadata (
        override_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        new_verdict VARCHAR NOT NULL,
        override_type VARCHAR NOT NULL,
        reason VARCHAR NOT NULL,
        actor VARCHAR,
        created_at TIMESTAMPTZ NOT NULL
    )
    """
    ws_execute(sql)


def server_exists(server_id: str) -> bool:
    rows = ws_query(f"SELECT server_id FROM mcp_server_registry WHERE server_id = '{server_id}' LIMIT 1")
    return len(rows) > 0


def compute_override_id(server_id: str, new_verdict: str, override_type: str, ts: str) -> str:
    import hashlib
    raw = f"{server_id}:{new_verdict}:{override_type}:{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class OverrideRequest(BaseModel):
    new_verdict: str
    reason: str
    override_type: str
    actor: str | None = None


class OverrideResponse(BaseModel):
    success: bool
    override_id: str
    server_id: str
    new_verdict: str
    message: str


@app.post("/servers/{server_id}/override", response_model=OverrideResponse)
def apply_override(server_id: str, payload: OverrideRequest):
    if not server_exists(server_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found in registry."
        )

    ts = utc_now_iso()
    actor = payload.actor or "unknown"
    override_id = compute_override_id(server_id, payload.new_verdict, payload.override_type, ts)

    override_row = {
        "override_id": override_id,
        "server_id": server_id,
        "new_verdict": payload.new_verdict,
        "override_type": payload.override_type,
        "reason": payload.reason,
        "actor": actor,
        "created_at": ts
    }

    audit_row = {
        "event_id": override_id,
        "event_type": "manual_override",
        "actor": actor,
        "action": "verdict_override",
        "target_server_id": server_id,
        "details_json": f'{{"new_verdict": "{payload.new_verdict}", "override_type": "{payload.override_type}", "reason": "{payload.reason}"}}',
        "outcome": "success",
        "timestamp": ts,
        "immutable": True
    }

    ok1 = ws_write("override_metadata", [override_row])
    ok2 = ws_write("audit_log", [audit_row])

    if not (ok1 and ok2):
        log.error("Failed to write override or audit for server_id=%s", server_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist override. Write service may be unavailable."
        )

    log.info(
        "Override applied: override_id=%s server_id=%s new_verdict=%s actor=%s",
        override_id, server_id, payload.new_verdict, actor
    )

    return OverrideResponse(
        success=True,
        override_id=override_id,
        server_id=server_id,
        new_verdict=payload.new_verdict,
        message="Override applied and audit logged successfully."
    )


@app.get("/servers/{server_id}/overrides")
def list_overrides(server_id: str):
    rows = ws_query(
        f"SELECT override_id, server_id, new_verdict, override_type, reason, actor, created_at "
        f"FROM override_metadata WHERE server_id = '{server_id}' ORDER BY created_at DESC LIMIT 50"
    )
    return {"server_id": server_id, "overrides": rows}


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "port": SERVICE_PORT}


def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    ensure_tables()
    send_heartbeat()
    log.info("Starting %s on port %s", SERVICE_NAME, SERVICE_PORT)
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)


if __name__ == "__main__":
    run()