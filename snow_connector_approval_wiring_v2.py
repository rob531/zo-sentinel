import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
import os, time, json, logging, hashlib, hmac, requests, threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

SERVICE_NAME = "snow_connector_approval_wiring_v2"
SERVICE_PORT = 8788
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
HEARTBEAT_INTERVAL = 60
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"
SNOW_CONNECTOR_URL = "http://127.0.0.1:8778"
HEALTH_THRESHOLD_SECONDS = 120

SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")
SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")
SNOW_API_USER = os.environ.get("SNOW_API_USER", "")
SNOW_API_PASS = os.environ.get("SNOW_API_PASS", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(SERVICE_NAME)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

START_TIME = datetime.now(timezone.utc)

class SNOWWebhookPayload(BaseModel):
    ticket_number: str
    short_description: str
    description: Optional[str] = ""
    opened_by: Optional[str] = ""
    state: Optional[int] = 1
    assigned_to: Optional[str] = None
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    approval_status: Optional[str] = None
    opened_at: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = {}

class MCPRequestSubmission(BaseModel):
    server_id: Optional[str] = None
    server_name: str
    url: Optional[str] = ""
    description: Optional[str] = ""
    requestor: str
    ticket_number: Optional[str] = None

class ApprovalDecisionSync(BaseModel):
    server_id: str
    decision: str
    analyst_email: Optional[str] = None
    notes: Optional[str] = None
    decided_at: str

def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log.warning(f"{SERVICE_NAME} already running as PID {old_pid}")
            return False
        except (OSError, ValueError):
            log.info("Stale PID file found, removing")
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    log.info(f"PID {os.getpid()} written to {PID_FILE}")
    return True

def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
            log.info("PID file removed")
    except Exception as e:
        log.error(f"Failed to remove PID file: {e}")

def signal_handler(sig, frame):
    log.info(f"Received signal {sig}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

import signal
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def send_heartbeat():
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat()
            }
        }
        r = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        if r.status_code == 200:
            log.debug(f"Heartbeat sent at {datetime.now(timezone.utc).isoformat()}")
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")

def ws_query(sql: str, limit: int = 100) -> list:
    try:
        r = requests.post(QUERY_SERVICE_URL, json={"sql": sql, "limit": limit}, timeout=10)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query: {e}")
    return []

def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": row, "wait": True}, timeout=10)
        if r.status_code == 200:
            log.debug(f"ws_write {table}: ok")
            return True
        else:
            log.error(f"ws_write {table} failed: {r.status_code} {r.text}")
    except Exception as e:
        log.error(f"ws_write {table}: {e}")
    return False

def ws_execute(sql: str) -> bool:
    try:
        r = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_execute: {e}")
    return False

def verify_webhook_signature(payload_bytes: bytes, signature: str) -> bool:
    if not SNOW_WEBHOOK_SECRET:
        log.warning("SNOW_WEBHOOK_SECRET not set, skipping signature verification")
        return True
    expected = hmac.new(
        SNOW_WEBHOOK_SECRET.encode(),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")

def normalize_server_name(name: str) -> str:
    if not name:
        return "unknown-mcp-server"
    name = name.strip().lower()
    name = ''.join(c if c.isalnum() or c in '-_' else '-' for c in name)
    name = name.strip('-')
    if len(name) > 50:
        name = name[:50]
    return name or "unknown-mcp-server"

def generate_server_id(server_name: str) -> str:
    import uuid
    raw = f"{server_name}-{datetime.now(timezone.utc).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def ensure_tables():
    tables = [
        """CREATE TABLE IF NOT EXISTS snow_wiring_submissions (
            server_id VARCHAR,
            server_name VARCHAR,
            requestor VARCHAR,
            ticket_number VARCHAR,
            snow_state INTEGER,
            approval_status VARCHAR DEFAULT 'pending',
            submitted_at TIMESTAMP,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (server_id, ticket_number)
        )""",
        """CREATE TABLE IF NOT EXISTS snow_wiring_approvals (
            server_id VARCHAR,
            ticket_number VARCHAR,
            decision VARCHAR,
            analyst_email VARCHAR,
            notes TEXT,
            decided_at TIMESTAMP,
            synced_to_snow BOOLEAN DEFAULT FALSE,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (server_id, ticket_number, decided_at)
        )"""
    ]
    for sql in tables:
        ws_execute(sql)
    log.info("Wiring tables ensured")

def get_pending_approvals_from_workflow() -> List[Dict]:
    try:
        r = requests.get(f"{APPROVAL_WORKFLOW_URL}/api/submissions?status=decided", timeout=10)
        if r.status_code == 200:
            return r.json().get("submissions", [])
    except Exception as e:
        log.error(f"Failed to fetch pending approvals: {e}")
    return []

def submit_to_approval_workflow(submission: Dict) -> Optional[Dict]:
    try:
        r = requests.post(
            f"{APPROVAL_WORKFLOW_URL}/api/submit",
            json=submission,
            timeout=15
        )
        if r.status_code in (200, 201):
            return r.json()
        else:
            log.error(f"Approval workflow rejected submission: {r.status_code} {r.text}")
    except Exception as e:
        log.error(f"Failed to submit to approval workflow: {e}")
    return None

def update_snow_ticket(ticket_number: str, state: int, approval_status: str) -> bool:
    if not SNOW_INSTANCE:
        log.warning("SNOW_INSTANCE not configured, skipping SNOW update")
        return False
    try:
        auth = None
        if SNOW_API_USER and SNOW_API_PASS:
            auth = (SNOW_API_USER, SNOW_API_PASS)
        update_data = {
            "state": state,
            "u_approval_status": approval_status,
            "u_sentinel_synced": True
        }
        r = requests.patch(
            f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/rm_it_mcp/{ticket_number}",
            json=update_data,
            headers={"Content-Type": "application/json"},
            auth=auth,
            timeout=15
        )
        if r.status_code in (200, 204):
            log.info(f"Updated SNOW ticket {ticket_number}: state={state}, status={approval_status}")
            return True
        else:
            log.error(f"SNOW ticket update failed: {r.status_code} {r.text}")
    except Exception as e:
        log.error(f"Failed to update SNOW ticket {ticket_number}: {e}")
    return False

def sync_approval_decisions():
    try:
        result = ws_query("""
            SELECT server_id, ticket_number, decision, analyst_email, notes, decided_at
            FROM snow_wiring_approvals
            WHERE synced_to_snow = FALSE
            ORDER BY decided_at ASC
            LIMIT 50
        """)
        if not result:
            return
        synced = 0
        for row in result:
            state = 3 if row.get("decision") == "APPROVED" else (4 if row.get("decision") == "REJECTED" else 2)
            approval_status = row.get("decision", "pending").upper()
            if update_snow_ticket(row.get("ticket_number", ""), state, approval_status):
                ws_write("snow_wiring_approvals", {
                    "server_id": row.get("server_id"),
                    "ticket_number": row.get("ticket_number"),
                    "decision": row.get("decision"),
                    "analyst_email": row.get("analyst_email"),
                    "notes": row.get("notes"),
                    "decided_at": row.get("decided_at"),
                    "synced_to_snow": True
                })
                synced += 1
        if synced > 0:
            log.info(f"Synced {synced} approval decisions to SNOW")
    except Exception as e:
        log.error(f"Approval sync failed: {e}")

def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def sync_loop():
    while True:
        try:
            sync_approval_decisions()
        except Exception as e:
            log.error(f"Sync loop error: {e}")
        time.sleep(30)

def get_health_status() -> Dict:
    age = (datetime.now(timezone.utc) - START_TIME).total_seconds()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "port": SERVICE_PORT,
        "uptime_seconds": int(age)
    }

@app.get("/health")
async def health():
    return get_health_status()

@app.post("/snow/inbound")
async def snow_inbound_webhook(request: Request):
    body = await request.body()
    log.info(f"SNOW inbound webhook received, size={len(body)}")
    try:
        payload = json.loads(body)
    except Exception as e:
        log.error(f"Invalid JSON from SNOW: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    ticket_number = payload.get("ticket_number", "unknown")
    short_desc = payload.get("short_description", "")
    server_name = normalize_server_name(short_desc)
    server_id = payload.get("server_id") or generate_server_id(server_name)
    requestor = payload.get("opened_by", "unknown")
    snow_state = payload.get("state", 1)
    ws_write("snow_wiring_submissions", {
        "server_id": server_id,
        "server_name": server_name,
        "requestor": requestor,
        "ticket_number": ticket_number,
        "snow_state": snow_state,
        "approval_status": "pending",
        "submitted_at": datetime.now(timezone.utc).isoformat()
    })
    submission = {
        "server_id": server_id,
        "server_name": server_name,
        "url": payload.get("server_url", ""),
        "description": payload.get("description", ""),
        "requestor": requestor,
        "ticket_number": ticket_number
    }
    result = submit_to_approval_workflow(submission)
    if not result:
        raise HTTPException(status_code=502, detail="Failed to submit to approval_workflow")
    log.info(f"SNOW ticket {ticket_number} -> approval_workflow submission {result.get('submission_id')}")
    return {"ok": True, "submission_id": result.get("submission_id"), "server_id": server_id}

@app.post("/snow/decision")
async def snow_decision_webhook(request: Request):
    body = await request.body()
    log.info(f"SNOW decision webhook received, size={len(body)}")
    try:
        payload = json.loads(body)
    except Exception as e:
        log.error(f"Invalid JSON from SNOW decision: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    server_id = payload.get("server_id")
    decision = payload.get("decision", "pending")
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id required")
    ticket_number = payload.get("ticket_number", "")
    analyst_email = payload.get("analyst_email", "")
    notes = payload.get("notes", "")
    decided_at = datetime.now(timezone.utc).isoformat()
    ws_write("snow_wiring_approvals", {
        "server_id": server_id,
        "ticket_number": ticket_number,
        "decision": decision.upper(),
        "analyst_email": analyst_email,
        "notes": notes,
        "decided_at": decided_at
    })
    log.info(f"Recorded decision {decision} for server {server_id} from SNOW ticket {ticket_number}")
    sync_approval_decisions()
    return {"ok": True, "synced": True}

@app.get("/snow/ticket/{ticket_number}")
async def get_ticket_status(ticket_number: str):
    result = ws_query(f"""
        SELECT server_id, server_name, approval_status, snow_state, submitted_at
        FROM snow_wiring_submissions
        WHERE ticket_number = '{ticket_number}'
        ORDER BY submitted_at DESC
        LIMIT 1
    """)
    if result:
        return {"ok": True, "ticket": result[0]}
    return {"ok": True, "ticket": None}

@app.get("/snow/server/{server_id}/status")
async def get_server_approval_status(server_id: str):
    submission = ws_query(f"""
        SELECT approval_status, ticket_number, submitted_at
        FROM snow_wiring_submissions
        WHERE server_id = '{server_id}'
        ORDER BY submitted_at DESC
        LIMIT 1
    """)
    decision = ws_query(f"""
        SELECT decision, analyst_email, notes, decided_at
        FROM snow_wiring_approvals
        WHERE server_id = '{server_id}'
        ORDER BY decided_at DESC
        LIMIT 1
    """)
    return {
        "ok": True,
        "server_id": server_id,
        "current_submission": submission[0] if submission else None,
        "latest_decision": decision[0] if decision else None
    }

def run():
    if not check_single_instance():
        sys.exit(1)
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    ensure_tables()
    threading.Thread(target=heartbeat_loop, daemon=True, name="heartbeat").start()
    threading.Thread(target=sync_loop, daemon=True, name="sync").start()
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT, log_level="info")

if __name__ == "__main__":
    run()