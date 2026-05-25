#!/usr/bin/env python3
"""
snow_connector_wiring_complete.py - ZO-SENTINEL Phase 9
Complete wiring of snow_connector.py into approval_workflow.

Per Appendix A: Routes ServiceNow inbound webhook data through the snow_connector
integration layer to update mcp_submissions and audit_log tables.
Validates request signatures per Section 7.

All DB writes via write_service (port 8772) - no direct DuckDB access.
Port: 8791
"""
import os
import sys
import time
import json
import hashlib
import hmac
import base64
import logging
import signal
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import requests
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Header, Depends, status
from pydantic import BaseModel
from pydantic.main import BaseModel as PydanticBaseModel

SERVICE_NAME = "snow_connector_wiring_complete"
SERVICE_PORT = 8791
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"

PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 30
PROCESSED_EVENTS_FILE = "/tmp/snow_wiring_processed_events.json"

SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")
SNOW_INSTANCE_URL = os.environ.get("SNOW_INSTANCE_URL", "")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)

app = FastAPI(title=f"{SERVICE_NAME} API", version="1.0.0")
_start_time = time.time()
_token_cache: Dict[str, Any] = {}
_processed_events: set = set()
_should_shutdown = False


class SnowTicketEvent(BaseModel):
    ticket_id: str
    short_description: Optional[str] = ""
    description: Optional[str] = ""
    state: Optional[str] = ""
    u_mcp_server_name: Optional[str] = ""
    u_requested_by: Optional[str] = ""
    u_decision: Optional[str] = ""
    u_verdict: Optional[str] = ""
    sys_id: Optional[str] = ""
    number: Optional[str] = ""
    event_type: Optional[str] = "ticket_update"
    timestamp: Optional[str] = ""


def log_msg(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    log_func = getattr(log, level.lower(), log.info)
    log_func(f"[{ts}] {msg}")


def check_single_instance() -> bool:
    pid = os.getpid()
    try:
        with open(PID_FILE, 'r') as f:
            existing_pid = int(f.read().strip())
            if existing_pid != pid:
                try:
                    os.kill(existing_pid, 0)
                    log.error(f"Service already running with PID {existing_pid}")
                    return False
                except OSError:
                    log.warning(f"Stale PID file found, overwriting")
    except FileNotFoundError:
        pass
    
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    log.info(f"Service started with PID {pid}")
    return True


def remove_pid_file():
    try:
        os.remove(PID_FILE)
        log.info("PID file removed")
    except FileNotFoundError:
        pass


def signal_handler(signum, frame):
    global _should_shutdown
    log.warning(f"Received signal {signum}, initiating shutdown")
    _should_shutdown = True


def load_processed_events():
    global _processed_events
    try:
        if os.path.exists(PROCESSED_EVENTS_FILE):
            with open(PROCESSED_EVENTS_FILE, 'r') as f:
                _processed_events = set(json.load(f))
            log.info(f"Loaded {len(_processed_events)} processed events")
    except Exception as e:
        log.warning(f"Could not load processed events: {e}")


def save_processed_events():
    try:
        with open(PROCESSED_EVENTS_FILE, 'w') as f:
            json.dump(list(_processed_events), f)
    except Exception as e:
        log.error(f"Could not save processed events: {e}")


def ws_write(table: str, row: dict) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": row, "wait": True},
            timeout=15
        )
        if resp.status_code == 200:
            return True
        log.error(f"ws_write failed for {table}: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        log.error(f"ws_write exception for {table}: {e}")
        return False


def ws_query(sql: str, limit: int = 100) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql, "limit": limit},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        log.error(f"ws_query failed: {resp.status_code} - {resp.text}")
        return []
    except Exception as e:
        log.error(f"ws_query exception: {e}")
        return []


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=15
        )
        if resp.status_code == 200:
            return True
        log.error(f"ws_execute failed: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        log.error(f"ws_execute exception: {e}")
        return False


def send_heartbeat():
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat()
                },
                "wait": True
            },
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")
        return False


def ensure_tables():
    tables = [
        """
        CREATE TABLE IF NOT EXISTS snow_ticket_events (
            event_id VARCHAR PRIMARY KEY,
            ticket_id VARCHAR,
            short_description VARCHAR,
            description VARCHAR,
            state VARCHAR,
            u_mcp_server_name VARCHAR,
            u_requested_by VARCHAR,
            u_decision VARCHAR,
            u_verdict VARCHAR,
            sys_id VARCHAR,
            number VARCHAR,
            event_type VARCHAR,
            processed_at VARCHAR,
            created_at VARCHAR DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS snow_submission_mapping (
            snow_ticket_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            submission_id VARCHAR,
            mapped_at VARCHAR,
            status VARCHAR
        )
        """
    ]
    for sql in tables:
        ws_execute(sql)


def validate_snow_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    if not secret:
        log.warning("SNOW_WEBHOOK_SECRET not configured, skipping signature validation")
        return True
    
    if not signature_header:
        return False
    
    expected = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    expected_b64 = base64.b64encode(
        hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).digest()
    ).decode('utf-8')
    
    return hmac.compare_digest(signature_header, f"v1={expected}") or \
           hmac.compare_digest(signature_header, expected_b64)


def verify_snow_webhook_signature(request_body: bytes, headers: dict, secret: str) -> bool:
    signature = headers.get("x-snow-signature") or headers.get("x-hub-signature-256") or ""
    
    if signature.startswith("sha256="):
        signature = signature[7:]
    
    return validate_snow_signature(request_body, f"v1={signature}", secret)


def sanitize_for_log(text: str) -> str:
    if not text:
        return "[EMPTY]"
    return f"[LENGTH:{len(text)}]"


def is_token_fresh(token_expiry: Optional[str]) -> bool:
    if not token_expiry:
        return False
    try:
        expiry_time = datetime.fromisoformat(token_expiry.replace('Z', '+00:00'))
        return datetime.now(timezone.utc) < expiry_time - timedelta(minutes=5)
    except Exception:
        return False


def get_dedup_key(event: SnowTicketEvent) -> str:
    base = f"{event.ticket_id}:{event.sys_id}:{event.event_type}"
    if event.number:
        base = f"{event.number}:{base}"
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def find_server_by_ticket_correlation(ticket_id: str, mcp_server_name: str) -> Optional[Dict[str, Any]]:
    if mcp_server_name:
        sql = f"""
            SELECT server_id, name FROM mcp_server_registry 
            WHERE LOWER(name) = LOWER('{mcp_server_name.replace("'", "''")}')
            LIMIT 1
        """
        results = ws_query(sql)
        if results:
            return results[0]
    
    sql = f"""
        SELECT server_id, name FROM mcp_server_registry 
        WHERE description LIKE '%{ticket_id.replace("'", "''")}%'
        LIMIT 1
    """
    results = ws_query(sql)
    if results:
        return results[0]
    
    return None


def map_ticket_to_submission(ticket_id: str, server_id: Optional[str], submission_id: Optional[str], status: str) -> bool:
    mapping_row = {
        "snow_ticket_id": ticket_id,
        "server_id": server_id or "",
        "submission_id": submission_id or "",
        "mapped_at": datetime.now(timezone.utc).isoformat(),
        "status": status
    }
    return ws_write("snow_submission_mapping", mapping_row)


def record_snow_event(event: SnowTicketEvent) -> bool:
    event_id = f"snow_{event.ticket_id}_{int(time.time())}"
    event_row = {
        "event_id": event_id,
        "ticket_id": event.ticket_id,
        "short_description": event.short_description or "",
        "description": event.description or "",
        "state": event.state or "",
        "u_mcp_server_name": event.u_mcp_server_name or "",
        "u_requested_by": event.u_requested_by or "",
        "u_decision": event.u_decision or "",
        "u_verdict": event.u_verdict or "",
        "sys_id": event.sys_id or "",
        "number": event.number or "",
        "event_type": event.event_type or "ticket_update",
        "processed_at": datetime.now(timezone.utc).isoformat()
    }
    return ws_write("snow_ticket_events", event_row)


def write_audit_log(
    target_server_id: Optional[str],
    event_type: str,
    actor: str,
    detail: str,
    ticket_id: Optional[str] = None
) -> bool:
    audit_row = {
        "target_server_id": target_server_id or "",
        "event_type": event_type,
        "actor": actor,
        "detail": detail[:500] if detail else "",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    if ticket_id:
        audit_row["detail"] = f"[SNOW:{ticket_id}] {detail}"
    
    return ws_write("audit_log", audit_row)


def update_submission_status(server_id: str, submission_id: Optional[str], 
                             decision: str, verdict: str, ticket_id: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    
    if submission_id:
        sql = f"""
            UPDATE mcp_submissions 
            SET status = 'reviewed',
                decision = '{decision.replace("'", "''")}',
                verdict = '{verdict.replace("'", "''")}',
                reviewed_by = 'snow_connector',
                reviewed_at = '{now}'
            WHERE submission_id = '{submission_id.replace("'", "''")}'
        """
    else:
        sql = f"""
            UPDATE mcp_submissions 
            SET status = 'reviewed',
                decision = '{decision.replace("'", "''")}',
                verdict = '{verdict.replace("'", "''")}',
                reviewed_by = 'snow_connector',
                reviewed_at = '{now}'
            WHERE server_id = '{server_id.replace("'", "''")}'
            AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
        """
    
    return ws_execute(sql)


def update_registry_verdict(server_id: str, verdict: str) -> bool:
    verdict_score_map = {
        "TRUSTED": 1.0,
        "CONDITIONAL": 0.6,
        "REJECTED": 0.0,
        "PENDING": 0.5
    }
    trust_score = verdict_score_map.get(verdict.upper(), 0.5)
    
    sql = f"""
        UPDATE mcp_server_registry 
        SET verdict = '{verdict.replace("'", "''")}',
            trust_score = {trust_score},
            scan_count = scan_count + 1
        WHERE server_id = '{server_id.replace("'", "''")}'
    """
    return ws_execute(sql)


def process_ticket_event(event: SnowTicketEvent) -> Dict[str, Any]:
    dedup_key = get_dedup_key(event)
    
    if dedup_key in _processed_events:
        log.info(f"Skipping duplicate event: {dedup_key}")
        return {"status": "duplicate", "event_id": dedup_key}
    
    _processed_events.add(dedup_key)
    if len(_processed_events) > 10000:
        _processed_events = set(list(_processed_events)[-5000:])
    save_processed_events()
    
    if not record_snow_event(event):
        log.error(f"Failed to record SNOW event for ticket {event.ticket_id}")
        return {"status": "error", "message": "Failed to record event"}
    
    log.info(f"Processing SNOW ticket: {event.ticket_id} for MCP: {event.u_mcp_server_name}")
    
    server = find_server_by_ticket_correlation(event.ticket_id, event.u_mcp_server_name)
    
    if not server and event.u_mcp_server_name:
        log.warning(f"No server found for MCP name: {event.u_mcp_server_name}")
        write_audit_log(
            None, "snow_ticket_unmatched", "snow_connector",
            f"SNOW ticket {event.ticket_id} could not be matched to any server: {event.u_mcp_server_name}",
            event.ticket_id
        )
        return {"status": "unmatched", "ticket_id": event.ticket_id}
    
    server_id = server["server_id"] if server else ""
    mcp_name = server["name"] if server else event.u_mcp_server_name or "unknown"
    
    submission_sql = f"""
        SELECT submission_id FROM mcp_submissions 
        WHERE server_id = '{server_id.replace("'", "''")}'
        ORDER BY created_at DESC LIMIT 1
    """ if server_id else f"""
        SELECT submission_id FROM mcp_submissions 
        WHERE mcp_name = '{event.u_mcp_server_name.replace("'", "''")}'
        ORDER BY created_at DESC LIMIT 1
    """
    
    submission_result = ws_query(submission_sql)
    submission_id = submission_result[0]["submission_id"] if submission_result else None
    
    decision = event.u_decision or "PENDING"
    verdict = event.u_verdict or decision
    
    if event.state in ["7", "Closed", "Resolved", "3"]:
        if not submission_id:
            submission_row = {
                "submission_id": f"sn_{event.ticket_id}",
                "server_id": server_id,
                "mcp_name": mcp_name,
                "description": event.description or event.short_description or "",
                "url": f"https://{SNOW_INSTANCE_URL}/nav_to.do?uri=incident.do?sys_id={event.sys_id}" if event.sys_id else "",
                "submitted_by": event.u_requested_by or "snow_webhook",
                "submitted_at": event.timestamp or datetime.now(timezone.utc).isoformat(),
                "status": "reviewed",
                "decision": decision,
                "verdict": verdict,
                "reviewed_by": "snow_connector",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "ticket_id": event.ticket_id
            }
            ws_write("mcp_submissions", submission_row)
        else:
            update_submission_status(server_id, submission_id, decision, verdict, event.ticket_id)
        
        if server_id:
            update_registry_verdict(server_id, verdict)
        
        write_audit_log(
            server_id, "snow_verdict_received", "snow_connector",
            f"SNOW ticket {event.ticket_id} updated with verdict: {verdict} (decision: {decision})",
            event.ticket_id
        )
        
        log.info(f"Updated verdict for server {server_id} to {verdict}")
        
        map_ticket_to_submission(event.ticket_id, server_id, submission_id, verdict)
        
        return {
            "status": "processed",
            "server_id": server_id,
            "verdict": verdict,
            "ticket_id": event.ticket_id
        }
    
    write_audit_log(
        server_id, "snow_ticket_updated", "snow_connector",
        f"SNOW ticket {event.ticket_id} state updated to: {event.state}",
        event.ticket_id
    )
    
    map_ticket_to_submission(event.ticket_id, server_id, submission_id, f"state_{event.state}")
    
    return {
        "status": "acknowledged",
        "ticket_id": event.ticket_id,
        "state": event.state
    }


def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    sql = f"""
        SELECT verdict, trust_score FROM mcp_server_registry 
        WHERE server_id = '{server_id.replace("'", "''")}'
    """
    results = ws_query(sql)
    return results[0] if results else None


def get_ticket_status(ticket_id: str) -> Optional[Dict[str, Any]]:
    sql = f"""
        SELECT status, u_verdict, u_decision, state 
        FROM snow_ticket_events 
        WHERE ticket_id = '{ticket_id.replace("'", "''")}'
        ORDER BY processed_at DESC LIMIT 1
    """
    results = ws_query(sql)
    return results[0] if results else None


@app.get("/health")
def health():
    uptime = int(time.time() - _start_time)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime_seconds": uptime
    }


@app.get("/ready")
def ready():
    try:
        test_query = ws_query("SELECT 1 as test LIMIT 1")
        if test_query:
            return {"status": "ready", "write_service": "connected"}
        return {"status": "not_ready", "write_service": "disconnected"}
    except Exception as e:
        return {"status": "not_ready", "error": str(e)}


@app.post("/webhook/snow")
async def receive_snow_webhook(request: Request):
    body = await request.body()
    
    signature = request.headers.get("x-snow-signature") or \
                request.headers.get("x-hub-signature-256") or ""
    
    if SNOW_WEBHOOK_SECRET and SNOW_WEBHOOK_SECRET != "SNOW_WEBHOOK_SECRET":
        if not verify_snow_webhook_signature(body, dict(request.headers), SNOW_WEBHOOK_SECRET):
            log.warning(f"Invalid SNOW webhook signature from {request.client}")
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    try:
        payload = await request.json()
    except Exception:
        log.error("Failed to parse webhook payload")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    event = SnowTicketEvent(
        ticket_id=payload.get("ticket_id", payload.get("u_ticket_id", "")),
        short_description=payload.get("short_description", ""),
        description=payload.get("description", ""),
        state=payload.get("state", payload.get("u_state", "")),
        u_mcp_server_name=payload.get("u_mcp_server_name", payload.get("short_description", "")),
        u_requested_by=payload.get("u_requested_by", payload.get("opened_by", "")),
        u_decision=payload.get("u_decision", ""),
        u_verdict=payload.get("u_verdict", ""),
        sys_id=payload.get("sys_id", ""),
        number=payload.get("number", ""),
        event_type=payload.get("event_type", "ticket_update"),
        timestamp=payload.get("sys_updated_on", datetime.now(timezone.utc).isoformat())
    )
    
    if not event.ticket_id and not event.sys_id:
        log.error("Webhook missing ticket_id and sys_id")
        raise HTTPException(status_code=400, detail="Missing ticket identifier")
    
    result = process_ticket_event(event)
    
    return {"status": "received", "result": result}


@app.post("/submit/ticket")
def submit_ticket_for_review(
    mcp_server_name: str,
    ticket_id: str,
    short_description: str,
    description: Optional[str] = "",
    requested_by: Optional[str] = ""
):
    submission_id = f"sn_{ticket_id}"
    
    submission_row = {
        "submission_id": submission_id,
        "server_id": "",
        "mcp_name": mcp_server_name,
        "description": short_description,
        "url": "",
        "submitted_by": requested_by or "snow_connector",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "decision": "PENDING",
        "verdict": "PENDING",
        "ticket_id": ticket_id
    }
    
    ws_write("mcp_submissions", submission_row)
    
    write_audit_log(
        None, "snow_ticket_submitted", "snow_connector",
        f"SNOW ticket {ticket_id} submitted for review: {mcp_server_name}",
        ticket_id
    )
    
    map_ticket_to_submission(ticket_id, "", submission_id, "pending")
    
    return {
        "status": "submitted",
        "submission_id": submission_id,
        "ticket_id": ticket_id
    }


@app.get("/ticket/{ticket_id}")
def get_ticket_info(ticket_id: str):
    status_info = get_ticket_status(ticket_id)
    if not status_info:
        return {"status": "not_found", "ticket_id": ticket_id}
    return {"status": "found", "ticket_id": ticket_id, **status_info}


@app.get("/ticket/{ticket_id}/verdict")
def get_ticket_verdict(ticket_id: str):
    status_info = get_ticket_status(ticket_id)
    if not status_info:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {
        "ticket_id": ticket_id,
        "verdict": status_info.get("u_verdict", status_info.get("status", "UNKNOWN")),
        "decision": status_info.get("u_decision", "UNKNOWN"),
        "state": status_info.get("state", "UNKNOWN")
    }


@app.post("/sync/submission/{submission_id}")
def sync_submission_with_ticket(submission_id: str, ticket_id: str):
    sql = f"""
        SELECT server_id, mcp_name, status, verdict FROM mcp_submissions 
        WHERE submission_id = '{submission_id.replace("'", "''")}'
    """
    results = ws_query(sql)
    
    if not results:
        raise HTTPException(status_code=404, detail="Submission not found")
    
    submission = results[0]
    
    if submission.get("verdict") and submission["verdict"] != "PENDING":
        ticket_status = get_ticket_status(ticket_id)
        if ticket_status:
            return {
                "status": "already_reviewed",
                "submission_id": submission_id,
                "ticket_id": ticket_id,
                "verdict": submission["verdict"]
            }
    
    result = update_submission_status(
        submission["server_id"],
        submission_id,
        submission.get("decision", "PENDING"),
        submission.get("verdict", "PENDING"),
        ticket_id
    )
    
    return {
        "status": "synced" if result else "sync_failed",
        "submission_id": submission_id,
        "ticket_id": ticket_id
    }


def run():
    log.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log.error("Failed to acquire PID lock, exiting")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    load_processed_events()
    ensure_tables()
    
    heartbeat_count = 0
    cycle_count = 0
    
    log.info(f"{SERVICE_NAME} initialization complete")
    
    while not _should_shutdown:
        try:
            cycle_count += 1
            
            if heartbeat_count % HEARTBEAT_INTERVAL == 0:
                send_heartbeat()
            
            heartbeat_count += 1
            
            if cycle_count % 100 == 0:
                log.info(f"{SERVICE_NAME} heartbeat cycle {cycle_count}")
            
            time.sleep(POLL_SECS)
            
        except Exception as e:
            log.error(f"Error in main loop: {e}")
            time.sleep(POLL_SECS)
    
    log.info(f"{SERVICE_NAME} shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


if __name__ == '__main__':
    run()