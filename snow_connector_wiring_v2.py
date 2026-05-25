import os
import hmac
import hashlib
import time
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, Header
from pydantic import BaseModel
import uvicorn

SERVICE_NAME = "snow_inbound_webhook_receiver"
SERVICE_PORT = 8782
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/zo_sentinel/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 30

os.makedirs(LOG_DIR, exist_ok=True)

class SNOWTicketPayload(BaseModel):
    ticket_id: str
    short_description: str
    description: str
    state: str
    assigned_to: Optional[str] = None
    opened_by: Optional[str] = None
    opened_at: Optional[str] = None
    priority: Optional[str] = None

def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    log_line = f"[{timestamp}] [{level}] {message}\n"
    with open(LOG_FILE, "a") as f:
        f.write(log_line)
    print(log_line.strip())

def signal_handler(signum, frame):
    log(f"Received signal {signum}, shutting down gracefully")
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass
    exit(0)

def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log(f"Another instance already running with PID {old_pid}", "ERROR")
            return False
        except (OSError, ProcessLookupError, ValueError):
            log(f"Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log(f"Error removing PID file: {e}", "ERROR")

def get_write_url() -> str:
    return WRITE_SERVICE_URL

def get_query_url() -> str:
    return QUERY_SERVICE_URL

def get_execute_url() -> str:
    return EXECUTE_SERVICE_URL

def ws_write(table: str, rows: list) -> dict:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        import requests
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Write service error for table {table}: {e}", "ERROR")
        raise

def ws_query(sql: str) -> dict:
    payload = {"sql": sql}
    try:
        import requests
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Query service error: {e}", "ERROR")
        return {"rows": [], "count": 0}

def ws_execute(sql: str) -> dict:
    payload = {"sql": sql}
    try:
        import requests
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"Execute service error: {e}", "ERROR")
        raise

def send_heartbeat():
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.now(timezone.utc).isoformat()
        }])
    except Exception as e:
        log(f"Heartbeat failed: {e}", "ERROR")

def validate_snow_signature(payload_bytes: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        log("Missing signature or secret", "WARNING")
        return False
    expected = hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)

def extract_server_name_from_description(description: str) -> Optional[str]:
    if not description:
        return None
    description_lower = description.lower()
    markers = [
        "mcp server:",
        "server_name:",
        "server:",
        "mcp:",
        "name:",
        "mcp_name:"
    ]
    for marker in markers:
        if marker in description_lower:
            start_idx = description_lower.find(marker) + len(marker)
            end_idx = description.find('\n', start_idx)
            if end_idx == -1:
                end_idx = start_idx + 100
            server_name = description[start_idx:end_idx].strip()
            if server_name and len(server_name) < 200:
                return server_name
    lines = description.split('\n')
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and 3 < len(line) < 200:
            return line
    return description[:100].strip() if len(description) > 100 else description.strip()

def get_server_by_name(server_name: str) -> Optional[Dict[str, Any]]:
    result = ws_query(f"SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry WHERE LOWER(name) = LOWER('{server_name.replace('\'', '\'\'')}') LIMIT 1")
    rows = result.get("rows", [])
    return rows[0] if rows else None

def get_server_verdict(server_id: str) -> Dict[str, Any]:
    result = ws_query(f"SELECT verdict, trust_score, risk_tier FROM mcp_risk_register WHERE server_id = '{server_id}' LIMIT 1")
    rows = result.get("rows", [])
    if rows:
        return rows[0]
    return {"verdict": "UNKNOWN", "trust_score": None, "risk_tier": "UNASSESSED"}

def is_high_risk(verdict: str, risk_tier: str, trust_score: Optional[float]) -> bool:
    high_risk_verdicts = ["KNOWN_THREAT", "UNTRUSTED", "HIGH_RISK_ISOLATED"]
    high_risk_tiers = ["CRITICAL", "HIGH"]
    if verdict in high_risk_verdicts:
        return True
    if risk_tier in high_risk_tiers:
        return True
    if trust_score is not None and trust_score < 25:
        return True
    return False

def write_decision_record(server_id: str, server_name: str, ticket_id: str, decision: str, reason: str) -> bool:
    decision_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        ws_write("mcp_decisions", [{
            "decision_id": decision_id,
            "server_id": server_id,
            "server_name": server_name,
            "source": "SNOW_WEBHOOK",
            "ticket_id": ticket_id,
            "decision": decision,
            "reason": reason,
            "decided_by": "snow_inbound_webhook_receiver",
            "decided_at": now
        }])
        log(f"Written decision record: {decision_id} - {decision} for {server_name}")
        return True
    except Exception as e:
        log(f"Failed to write decision record: {e}", "ERROR")
        return False

def write_audit_log(server_id: str, server_name: str, event_type: str, action: str, detail: str, ticket_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        ws_write("audit_log", [{
            "target_server_id": server_id,
            "event_type": event_type,
            "actor": "snow_connector",
            "detail": f"{action}: {detail} | ticket_id={ticket_id}",
            "created_at": now
        }])
        log(f"Audit log entry written: {event_type} - {action}")
    except Exception as e:
        log(f"Failed to write audit log: {e}", "ERROR")

def ensure_tables():
    tables_to_create = [
        """
        CREATE TABLE IF NOT EXISTS mcp_decisions (
            decision_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            server_name VARCHAR,
            source VARCHAR,
            ticket_id VARCHAR,
            decision VARCHAR,
            reason VARCHAR,
            decided_by VARCHAR,
            decided_at TIMESTAMP
        )
        """
    ]
    for sql in tables_to_create:
        try:
            ws_execute(sql)
            log(f"Ensured table exists")
        except Exception as e:
            log(f"Table creation warning (may already exist): {e}", "WARNING")

app = FastAPI()

@app.post("/webhook/snow/ticket")
async def receive_snow_ticket(
    request: Request,
    x_snow_signature: Optional[str] = Header(None, alias="X-SNOW-Signature")
):
    payload_bytes = await request.body()
    snw_signing_secret = os.environ.get("SNOW_SIGNING_SECRET", "")
    
    if not snw_signing_secret:
        log("SNOW_SIGNING_SECRET not configured, rejecting webhook", "ERROR")
        raise HTTPException(status_code=500, detail="ServiceNow signing secret not configured")
    
    if not validate_snow_signature(payload_bytes, x_snow_signature or "", snw_signing_secret):
        log("Invalid or missing ServiceNow signature, rejecting webhook", "ERROR")
        write_audit_log(
            server_id="UNKNOWN",
            server_name="UNKNOWN",
            event_type="SNOW_WEBHOOK_REJECTED",
            action="SIGNATURE_VALIDATION_FAILED",
            detail=f"Missing or invalid X-SNOW-Signature header",
            ticket_id="UNKNOWN"
        )
        raise HTTPException(status_code=401, detail="Invalid or missing signature")
    
    try:
        payload = await request.json()
    except Exception as e:
        log(f"Failed to parse JSON payload: {e}", "ERROR")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    ticket_id = payload.get("ticket_id", "UNKNOWN")
    description = payload.get("description", "")
    short_description = payload.get("short_description", "")
    ticket_state = payload.get("state", "UNKNOWN")
    
    log(f"Received ServiceNow ticket: {ticket_id}, state={ticket_state}")
    
    write_audit_log(
        server_id="WEBHOOK",
        server_name="WEBHOOK",
        event_type="SNOW_WEBHOOK_RECEIVED",
        action="TICKET_RECEIVED",
        detail=f"Ticket {ticket_id}: {short_description[:100]}",
        ticket_id=ticket_id
    )
    
    if not description and not short_description:
        log("No description or short_description in ticket", "WARNING")
        write_audit_log(
            server_id="UNKNOWN",
            server_name="UNKNOWN",
            event_type="SNOW_WEBHOOK_REJECTED",
            action="EMPTY_DESCRIPTION",
            detail="Ticket has no description",
            ticket_id=ticket_id
        )
        return {"status": "rejected", "reason": "No description provided"}
    
    combined_text = f"{short_description}\n{description}"
    server_name = extract_server_name_from_description(combined_text)
    
    if not server_name:
        log("Could not extract MCP server name from ticket", "WARNING")
        write_audit_log(
            server_id="UNKNOWN",
            server_name="UNKNOWN",
            event_type="SNOW_WEBHOOK_REJECTED",
            action="SERVER_NAME_EXTRACTION_FAILED",
            detail="Could not extract server name from ticket description",
            ticket_id=ticket_id
        )
        return {"status": "rejected", "reason": "Could not extract MCP server name"}
    
    log(f"Extracted server name: {server_name}")
    
    server_record = get_server_by_name(server_name)
    
    if not server_record:
        log(f"Server not found in registry: {server_name}", "WARNING")
        write_audit_log(
            server_id="NOT_FOUND",
            server_name=server_name,
            event_type="SNOW_WEBHOOK_SERVER_NOT_FOUND",
            action="SERVER_NOT_IN_REGISTRY",
            detail=f"Server '{server_name}' not found in mcp_server_registry",
            ticket_id=ticket_id
        )
        return {
            "status": "rejected",
            "reason": "Server not found in registry",
            "server_name": server_name
        }
    
    server_id = server_record.get("server_id")
    verdict = server_record.get("verdict", "UNKNOWN")
    trust_score = server_record.get("trust_score")
    
    risk_info = get_server_verdict(server_id)
    risk_tier = risk_info.get("risk_tier", "UNASSESSED")
    
    if ticket_state in ["Closed", "Resolved", "3"]:
        if risk_tier == "UNASSESSED":
            risk_tier = "LOW"
    
    log(f"Server {server_name}: verdict={verdict}, risk_tier={risk_tier}, trust_score={trust_score}")
    
    if is_high_risk(verdict, risk_tier, trust_score):
        log(f"Server {server_name} is HIGH RISK, rejecting approval", "WARNING")
        write_audit_log(
            server_id=server_id,
            server_name=server_name,
            event_type="SNOW_WEBHOOK_REJECTED",
            action="HIGH_RISK_APPROVAL_BLOCKED",
            detail=f"verdict={verdict}, risk_tier={risk_tier}, trust_score={trust_score}",
            ticket_id=ticket_id
        )
        write_decision_record(
            server_id=server_id,
            server_name=server_name,
            ticket_id=ticket_id,
            decision="REJECTED",
            reason=f"High risk MCP blocked: verdict={verdict}, risk_tier={risk_tier}, trust_score={trust_score}"
        )
        return {
            "status": "rejected",
            "reason": "MCP is high risk and cannot be auto-approved",
            "server_name": server_name,
            "verdict": verdict,
            "risk_tier": risk_tier
        }
    
    if ticket_state in ["Closed", "Resolved", "3"]:
        log(f"Approving MCP {server_name} via ServiceNow ticket {ticket_id}", "INFO")
        write_audit_log(
            server_id=server_id,
            server_name=server_name,
            event_type="SNOW_WEBHOOK_APPROVED",
            action="MCP_APPROVED",
            detail=f"Approved via ServiceNow ticket closure",
            ticket_id=ticket_id
        )
        write_decision_record(
            server_id=server_id,
            server_name=server_name,
            ticket_id=ticket_id,
            decision="APPROVED",
            reason=f"Approved via ServiceNow ticket {ticket_id} in state {ticket_state}"
        )
        
        try:
            import requests
            approval_payload = {
                "server_id": server_id,
                "server_name": server_name,
                "source": "SNOW_WEBHOOK",
                "ticket_id": ticket_id,
                "verdict": verdict,
                "trust_score": trust_score
            }
            resp = requests.post(
                f"{APPROVAL_WORKFLOW_URL}/api/approve",
                json=approval_payload,
                timeout=15
            )
            if resp.status_code in [200, 201, 202]:
                log(f"Notified approval_workflow of MCP approval")
            else:
                log(f"approval_workflow notification returned {resp.status_code}", "WARNING")
        except Exception as e:
            log(f"Failed to notify approval_workflow: {e}", "WARNING")
        
        return {
            "status": "approved",
            "server_name": server_name,
            "server_id": server_id,
            "ticket_id": ticket_id,
            "verdict": verdict
        }
    else:
        log(f"Ticket {ticket_id} is in state {ticket_state}, not auto-approving", "INFO")
        write_audit_log(
            server_id=server_id,
            server_name=server_name,
            event_type="SNOW_WEBHOOK_PENDING",
            action="TICKET_NOT_CLOSED",
            detail=f"Ticket in state {ticket_state}, awaiting closure",
            ticket_id=ticket_id
        )
        return {
            "status": "pending",
            "reason": f"Ticket not closed (state: {ticket_state})",
            "server_name": server_name,
            "server_id": server_id
        }

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/")
async def root():
    return {
        "service": SERVICE_NAME,
        "version": "2.0",
        "endpoints": [
            "/webhook/snow/ticket",
            "/health"
        ]
    }

def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log(f"Heartbeat error: {e}", "ERROR")
        time.sleep(HEARTBEAT_INTERVAL)

import threading

def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log(f"Cannot start: another instance is running", "ERROR")
        exit(1)
    
    log(f"Starting {SERVICE_NAME}")
    
    ensure_tables()
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    log(f"{SERVICE_NAME} listening on port {SERVICE_PORT}")
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=SERVICE_PORT,
        log_level="info"
    )

if __name__ == "__main__":
    run()