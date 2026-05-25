import time
import json
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import requests
from fastapi import FastAPI, Request, HTTPException, Depends
from pydantic import BaseModel

# =============================================================================
# CONFIGURATION
# =============================================================================
SERVICE_NAME = "snow_integration_completion"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772"
SNOW_CONNECTOR_URL = "http://127.0.0.1:8783"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"
HEARTBEAT_INTERVAL = 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(SERVICE_NAME)

# =============================================================================
# SNOW OAUTH / SERVICE CONFIGURATION
# =============================================================================
SNOW_INSTANCE_URL = os.environ.get("SNOW_INSTANCE_URL", "https://your-instance.service-now.com")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")
SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")
SNOW_OAUTH_TOKEN_FILE = f"/tmp/{SERVICE_NAME}_oauth_token.json"

# =============================================================================
# APPLICATION SETUP
# =============================================================================
app = FastAPI()

# =============================================================================
# WEB SERVICE HELPERS
# =============================================================================

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"Query failed: {e}")
        return {"rows": [], "count": 0}

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"Write failed to {table}: {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"Execute failed: {e}")
        return False

def send_heartbeat() -> None:
    try:
        ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")

# =============================================================================
# SNOW OAUTH TOKEN MANAGEMENT
# =============================================================================

def get_snow_oauth_token() -> Optional[str]:
    token_file = SNOW_OAUTH_TOKEN_FILE
    try:
        if os.path.exists(token_file):
            with open(token_file, 'r') as f:
                token_data = json.load(f)
            expires_at = datetime.fromisoformat(token_data.get("expires_at", ""))
            if datetime.utcnow() < expires_at - timedelta(minutes=5):
                return token_data.get("access_token")
    except Exception as e:
        log.warning(f"Failed to read token file: {e}")

    if not SNOW_CLIENT_ID or not SNOW_CLIENT_SECRET:
        log.error("SNOW_CLIENT_ID and SNOW_CLIENT_SECRET must be set for OAuth authentication")
        return None

    try:
        auth_resp = requests.post(
            f"{SNOW_INSTANCE_URL}/oauth_token.do",
            data={
                "grant_type": "client_credentials",
                "client_id": SNOW_CLIENT_ID,
                "client_secret": SNOW_CLIENT_SECRET
            },
            timeout=30
        )
        auth_resp.raise_for_status()
        token_json = auth_resp.json()
        access_token = token_json.get("access_token")
        expires_in = int(token_json.get("expires_in", 3600))

        token_data = {
            "access_token": access_token,
            "expires_at": (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
        }
        with open(token_file, 'w') as f:
            json.dump(token_data, f)

        return access_token
    except Exception as e:
        log.error(f"Failed to obtain OAuth token: {e}")
        return None

# =============================================================================
# TICKET SUBMISSION TO SNOW
# =============================================================================

def submit_snow_ticket(server_id: str, mcp_name: str, description: str, urgency: str = "2") -> Optional[str]:
    access_token = get_snow_oauth_token()
    if not access_token:
        return None

    try:
        ticket_data = {
            "short_description": f"MCP Server Approval Review: {mcp_name}",
            "description": description,
            "urgency": urgency,
            "impact": "2",
            "category": "software",
            "assignment_group": "Security Operations",
            "correlation_id": f"mcp_{server_id}"
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        resp = requests.post(
            f"{SNOW_INSTANCE_URL}/api/now/table/incident",
            headers=headers,
            json=ticket_data,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()

        ticket_number = result.get("result", {}).get("number")
        if ticket_number:
            log.info(f"Created SNOW ticket {ticket_number} for server {server_id}")
            ws_write("audit_log", [{
                "target_server_id": server_id,
                "event_type": "snow_ticket_created",
                "actor": SERVICE_NAME,
                "detail": json.dumps({"ticket_number": ticket_number, "mcp_name": mcp_name}),
                "created_at": datetime.utcnow().isoformat()
            }])
            return ticket_number

        return None
    except Exception as e:
        log.error(f"Failed to submit SNOW ticket: {e}")
        return None

def update_snow_ticket(ticket_number: str, resolution_notes: str, state: str = "7") -> bool:
    access_token = get_snow_oauth_token()
    if not access_token:
        return False

    try:
        update_data = {
            "close_notes": resolution_notes,
            "state": state
        }

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        resp = requests.patch(
            f"{SNOW_INSTANCE_URL}/api/now/table/incident",
            headers=headers,
            params={"number": ticket_number},
            json=update_data,
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Failed to update SNOW ticket {ticket_number}: {e}")
        return False

# =============================================================================
# WEBHOOK SIGNATURE VALIDATION
# =============================================================================

def validate_webhook_signature(request: Request) -> bool:
    if not SNOW_WEBHOOK_SECRET:
        log.warning("SNOW_WEBHOOK_SECRET not configured - rejecting unsigned webhook")
        return False

    signature = request.headers.get("X-Snow-Signature")
    if not signature:
        log.warning("No signature header in webhook request - MUST reject unsigned webhooks")
        return False

    body = request.body()
    if isinstance(body, bytes):
        body = body.decode("utf-8")

    expected_signature = hmac.new(
        SNOW_WEBHOOK_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        log.warning("Webhook signature mismatch - rejecting tampered request")
        return False

    return True

# =============================================================================
# APPROVAL WORKFLOW INTEGRATION
# =============================================================================

def call_approval_workflow(server_id: str, snow_resolution: str, ticket_number: str) -> bool:
    try:
        payload = {
            "server_id": server_id,
            "resolution": snow_resolution,
            "ticket_number": ticket_number,
            "source": "snow_integration"
        }

        resp = requests.post(
            f"{APPROVAL_WORKFLOW_URL}/resolve",
            json=payload,
            timeout=30
        )
        resp.raise_for_status()

        log.info(f"Called approval_workflow for server {server_id} with SNOW resolution")
        ws_write("audit_log", [{
            "target_server_id": server_id,
            "event_type": "snow_resolution_applied",
            "actor": SERVICE_NAME,
            "detail": json.dumps({"ticket_number": ticket_number, "resolution": snow_resolution}),
            "created_at": datetime.utcnow().isoformat()
        }])
        return True
    except Exception as e:
        log.error(f"Failed to call approval_workflow: {e}")
        return False

# =============================================================================
# PENDING APPROVAL QUERY
# =============================================================================

def get_pending_approval_servers() -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, name, description, verdict, created_at
    FROM mcp_server_registry
    WHERE verdict = 'pending_approval'
    OR verdict = 'requires_external_review'
    ORDER BY created_at ASC
    """
    result = ws_query(sql)
    return result.get("rows", [])

# =============================================================================
# SNOW TICKET TRACKING
# =============================================================================

def ensure_snow_tickets_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS snow_ticket_tracking (
        server_id VARCHAR PRIMARY KEY,
        ticket_number VARCHAR,
        status VARCHAR,
        submitted_at VARCHAR,
        resolved_at VARCHAR,
        resolution_notes VARCHAR
    )
    """
    ws_execute(sql)

def track_ticket(server_id: str, ticket_number: str, status: str) -> None:
    sql = f"""
    INSERT INTO snow_ticket_tracking (server_id, ticket_number, status, submitted_at)
    VALUES ('{server_id}', '{ticket_number}', '{status}', '{datetime.utcnow().isoformat()}')
    ON CONFLICT (server_id) DO UPDATE SET
        ticket_number = '{ticket_number}',
        status = '{status}',
        submitted_at = '{datetime.utcnow().isoformat()}'
    """
    ws_execute(sql)

def update_ticket_status(server_id: str, status: str, resolution_notes: Optional[str] = None) -> None:
    resolution_clause = f", resolution_notes = '{resolution_notes}', resolved_at = '{datetime.utcnow().isoformat()}'" if resolution_notes else ""
    sql = f"""
    INSERT INTO snow_ticket_tracking (server_id, status{resolution_clause})
    VALUES ('{server_id}', '{status}'{resolution_clause})
    ON CONFLICT (server_id) DO UPDATE SET
        status = '{status}'{resolution_clause}
    """
    ws_execute(sql)

# =============================================================================
# API ENDPOINTS
# =============================================================================

class SnowWebhookPayload(BaseModel):
    ticket_number: str
    server_id: Optional[str] = None
    status: str
    resolution: Optional[str] = None
    closed_by: Optional[str] = None

@app.post("/webhook/snow")
async def snow_webhook(request: Request, payload: SnowWebhookPayload):
    if not await validate_webhook_signature(request):
        raise HTTPException(status_code=401, detail="Invalid webhook signature - MUST reject unsigned webhooks")

    log.info(f"Received SNOW webhook: ticket={payload.ticket_number}, status={payload.status}")

    ws_write("audit_log", [{
        "target_server_id": payload.server_id or "unknown",
        "event_type": "snow_webhook_received",
        "actor": "snow_webhook",
        "detail": json.dumps({"ticket_number": payload.ticket_number, "status": payload.status}),
        "created_at": datetime.utcnow().isoformat()
    }])

    if payload.server_id and payload.status in ["resolved", "closed"]:
        call_approval_workflow(payload.server_id, payload.resolution or "Approved via ServiceNow", payload.ticket_number)
        update_ticket_status(payload.server_id, "resolved", payload.resolution)

    return {"status": "received"}

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": datetime.utcnow().isoformat()}

@app.get("/pending")
def list_pending():
    servers = get_pending_approval_servers()
    return {"servers": servers, "count": len(servers)}

@app.post("/submit/{server_id}")
def submit_ticket(server_id: str):
    sql = f"SELECT name, description FROM mcp_server_registry WHERE server_id = '{server_id}'"
    result = ws_query(sql)
    rows = result.get("rows", [])
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    server = rows[0]
    description = f"Manual review required for MCP server: {server['name']}. Description: {server.get('description', 'N/A')}"

    ticket_number = submit_snow_ticket(server_id, server['name'], description)
    if ticket_number:
        track_ticket(server_id, ticket_number, "pending")
        return {"status": "submitted", "ticket_number": ticket_number}
    else:
        raise HTTPException(status_code=500, detail="Failed to submit SNOW ticket")

# =============================================================================
# DAEMON LIFECYCLE
# =============================================================================

def check_single_instance() -> bool:
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True

def remove_pid_file() -> None:
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    exit(0)

def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

import threading

def run() -> None:
    import os
    import signal

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        log.error("Cannot start - another instance is running")
        return

    ensure_snow_tickets_table()

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT)

if __name__ == "__main__":
    run()