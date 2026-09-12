import os
import sys
import logging
import time
import signal
import hashlib
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Header, Query
import uvicorn

SERVICE_NAME = "org_user_roster_api"
SERVICE_PORT = 8786
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

app = FastAPI()
log = logging.getLogger(__name__)

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
log.info("org_user_roster_api starting")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    payload = {"sql": sql}
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def ws_execute(sql: str) -> Dict[str, Any]:
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def verify_api_key(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    rows = ws_query(
        f"SELECT token_id, action, mcp_name, admin_email, expires_at, used "
        f"FROM auth_tokens WHERE token_id = '{token}'"
    )
    if not rows:
        raise HTTPException(status_code=401, detail="Invalid API token")
    record = rows[0]
    expires_at = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00")) if record.get("expires_at") else None
    if expires_at and datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=401, detail="Token expired")
    return record


def ensure_roster_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS org_user_roster (
        roster_id VARCHAR PRIMARY KEY,
        user_email VARCHAR NOT NULL,
        display_name VARCHAR,
        role VARCHAR NOT NULL DEFAULT 'analyst',
        organization VARCHAR NOT NULL DEFAULT 'default',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ,
        last_login_at TIMESTAMPTZ,
        UNIQUE(user_email, organization)
    )
    """
    ws_execute(sql)


def compute_roster_id(user_email: str, organization: str) -> str:
    raw = f"{user_email}:{organization}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@app.on_event("startup")
async def startup():
    ensure_roster_table()
    log.info("org_user_roster_api startup complete")


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}


@app.get("/roster")
async def list_roster(
    organization: str = Query(default="default"),
    is_active: bool = Query(default=None),
    role: str = Query(default=None),
    authorization: Optional[str] = Header(None),
):
    verify_api_key(authorization)
    conditions = [f"organization = '{organization}'"]
    if is_active is not None:
        conditions.append(f"is_active = {is_active}")
    if role:
        conditions.append(f"role = '{role}'")
    where_clause = " AND ".join(conditions)
    rows = ws_query(f"SELECT * FROM org_user_roster WHERE {where_clause} ORDER BY created_at DESC")
    return {"rows": rows, "count": len(rows), "ts": utc_now_iso()}


@app.get("/roster/{user_email}")
async def get_user(
    user_email: str,
    organization: str = Query(default="default"),
    authorization: Optional[str] = Header(None),
):
    verify_api_key(authorization)
    rows = ws_query(
        f"SELECT * FROM org_user_roster WHERE user_email = '{user_email}' AND organization = '{organization}'"
    )
    if not rows:
        raise HTTPException(status_code=404, detail="User not found in roster")
    return {"row": rows[0], "ts": utc_now_iso()}


@app.post("/roster")
async def add_user(
    payload: Dict[str, Any],
    authorization: Optional[str] = Header(None),
):
    verify_api_key(authorization)
    user_email = payload.get("user_email")
    display_name = payload.get("display_name", "")
    role = payload.get("role", "analyst")
    organization = payload.get("organization", "default")
    if not user_email:
        raise HTTPException(status_code=400, detail="user_email is required")
    allowed_roles = {"admin", "analyst", "viewer"}
    if role not in allowed_roles:
        raise HTTPException(status_code=400, detail=f"role must be one of: {allowed_roles}")
    roster_id = compute_roster_id(user_email, organization)
    now = utc_now_iso()
    rows = ws_query(
        f"SELECT roster_id FROM org_user_roster WHERE roster_id = '{roster_id}'"
    )
    if rows:
        raise HTTPException(status_code=409, detail="User already exists in roster")
    ws_write("org_user_roster", [{
        "roster_id": roster_id,
        "user_email": user_email,
        "display_name": display_name,
        "role": role,
        "organization": organization,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }])
    log.info(f"Added user {user_email} to roster {organization} with role {role}")
    return {"roster_id": roster_id, "ts": utc_now_iso()}


@app.put("/roster/{user_email}")
async def update_user(
    user_email: str,
    payload: Dict[str, Any],
    organization: str = Query(default="default"),
    authorization: Optional[str] = Header(None),
):
    verify_api_key(authorization)
    rows = ws_query(
        f"SELECT roster_id FROM org_user_roster WHERE user_email = '{user_email}' AND organization = '{organization}'"
    )
    if not rows:
        raise HTTPException(status_code=404, detail="User not found in roster")
    existing = ws_query(
        f"SELECT * FROM org_user_roster WHERE roster_id = '{rows[0]['roster_id']}'"
    )
    if not existing:
        raise HTTPException(status_code=404, detail="User record not found")
    record = existing[0]
    updates = []
    allowed_fields = {"display_name", "role", "is_active"}
    for field in allowed_fields:
        if field in payload:
            updates.append(f"{field} = ?")
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    now = utc_now_iso()
    update_parts = []
    for field in allowed_fields:
        if field in payload:
            val = payload[field]
            if isinstance(val, bool):
                update_parts.append(f"{field} = {val}")
            elif isinstance(val, str):
                update_parts.append(f"{field} = '{val}'")
            else:
                update_parts.append(f"{field} = {val}")
    update_parts.append(f"updated_at = '{now}'")
    update_sql = f"UPDATE org_user_roster SET {', '.join(update_parts)} WHERE roster_id = '{record['roster_id']}'"
    ws_execute(update_sql)
    log.info(f"Updated user {user_email} in roster {organization}")
    return {"roster_id": record["roster_id"], "ts": utc_now_iso()}


@app.delete("/roster/{user_email}")
async def remove_user(
    user_email: str,
    organization: str = Query(default="default"),
    authorization: Optional[str] = Header(None),
):
    verify_api_key(authorization)
    rows = ws_query(
        f"SELECT roster_id FROM org_user_roster WHERE user_email = '{user_email}' AND organization = '{organization}'"
    )
    if not rows:
        raise HTTPException(status_code=404, detail="User not found in roster")
    roster_id = rows[0]["roster_id"]
    ws_execute(f"DELETE FROM org_user_roster WHERE roster_id = '{roster_id}'")
    log.info(f"Removed user {user_email} from roster {organization}")
    return {"roster_id": roster_id, "ts": utc_now_iso()}


@app.get("/roster/{user_email}/activity")
async def get_user_activity(
    user_email: str,
    organization: str = Query(default="default"),
    authorization: Optional[str] = Header(None),
):
    verify_api_key(authorization)
    rows = ws_query(
        f"SELECT roster_id, last_login_at, is_active, role, updated_at "
        f"FROM org_user_roster WHERE user_email = '{user_email}' AND organization = '{organization}'"
    )
    if not rows:
        raise HTTPException(status_code=404, detail="User not found in roster")
    audit_rows = ws_query(
        f"SELECT event_type, action, outcome, timestamp FROM audit_log "
        f"WHERE actor = '{user_email}' ORDER BY timestamp DESC LIMIT 50"
    )
    return {"roster": rows[0], "audit_events": audit_rows, "ts": utc_now_iso()}


def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file {old_pid}, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "status": "running",
        "last_heartbeat": utc_now_iso(),
        "meta": "{}",
    }])


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()