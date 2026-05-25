#!/usr/bin/env python3
"""
snow_connector_completeness.py - ServiceNow Connector Wiring Completeness
Phase 9 integration work per spec Appendix A.

MUST: validate request signature
MUST: store SNOW ticket correlation ID
MUST NOT: auto-approve servers from webhooks without analyst review
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel

SERVICE_NAME = "snow_connector_completeness"
PORT = 8780
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
AUDIT_TABLE = "audit_log"
SNOW_WEBHOOK_SECRET = "SNOW_WEBHOOK_SECRET"
SNOW_CLIENT_ID = "SNOW_CLIENT_ID"
SNOW_CLIENT_SECRET = "SNOW_CLIENT_SECRET"
SNOW_INSTANCE_URL = "SNOW_INSTANCE_URL"
SNOW_REFRESH_TOKEN = "SNOW_REFRESH_TOKEN"
TOKEN_EXPIRY_SECONDS = 3600
HEARTBEAT_INTERVAL = 30
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

app = FastAPI()
_token_cache: Dict[str, Any] = {}
_start_time = time.time()
_processed_events: set = set()


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def check_single_instance() -> bool:
    try:
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        import os
        if old_pid > 0 and os.path.exists(f"/proc/{old_pid}"):
            log(f"Another instance running with PID {old_pid}, exiting.")
            return False
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"PID check error: {e}")
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(uuid.getpid()))
    except Exception as e:
        log(f"Failed to write PID file: {e}")
    return True


def remove_pid_file() -> None:
    try:
        import os
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame) -> None:
    log(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    import sys
    sys.exit(0)


def get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ws_write(table: str, rows: list) -> Dict[str, Any]:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_write error: {e}")
        return {"ok": False, "error": str(e)}


def ws_query(sql: str) -> Dict[str, Any]:
    payload = {"sql": sql}
    try:
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_query error: {e}")
        return {"rows": [], "count": 0, "error": str(e)}


def ws_execute(sql: str) -> Dict[str, Any]:
    payload = {"sql": sql}
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_execute error: {e}")
        return {"ok": False, "error": str(e)}


def send_heartbeat() -> None:
    try:
        payload = {
            "table": "service_health",
            "rows": [{"service": SERVICE_NAME, "last_heartbeat": get_utc_now_iso()}],
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        log(f"Heartbeat failed: {e}")


def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def ensure_audit_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY,
        target_server_id VARCHAR,
        event_type VARCHAR,
        actor VARCHAR,
        detail TEXT,
        created_at VARCHAR
    )
    """
    ws_execute(sql)


def ensure_snow_webhook_log_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS snow_webhook_log (
        id INTEGER PRIMARY KEY,
        webhook_id VARCHAR UNIQUE,
        snow_ticket_number VARCHAR,
        correlation_id VARCHAR,
        event_type VARCHAR,
        payload_hash VARCHAR,
        signature_valid BOOLEAN,
        server_id VARCHAR,
        processed BOOLEAN,
        action_taken VARCHAR,
        created_at VARCHAR
    )
    """
    ws_execute(sql)


def ensure_snow_token_cache_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS snow_token_cache (
        token_key VARCHAR PRIMARY KEY,
        access_token TEXT,
        expires_at VARCHAR,
        refreshed_at VARCHAR
    )
    """
    ws_execute(sql)


def compute_signature(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()


def validate_webhook_signature(
    payload: bytes,
    signature_header: str,
    secret: str
) -> bool:
    if not signature_header:
        log("Missing signature header")
        return False
    expected = compute_signature(payload, secret)
    valid = hmac.compare_digest(f"sha256={expected}", signature_header)
    if not valid:
        log(f"Signature mismatch: expected {expected}, got {signature_header}")
    return valid


def record_audit_log(
    event_type: str,
    actor: str,
    detail: str,
    target_server_id: Optional[str] = None
) -> None:
    now = get_utc_now_iso()
    result = ws_query("SELECT COALESCE(MAX(id), 0) + 1 as next_id FROM audit_log")
    next_id = result.get("rows", [{}])[0].get("next_id", 1)
    ws_write("audit_log", [{
        "id": next_id,
        "target_server_id": target_server_id or "N/A",
        "event_type": event_type,
        "actor": actor,
        "detail": json.dumps(detail),
        "created_at": now
    }])


def record_snow_webhook_log(
    webhook_id: str,
    snow_ticket_number: str,
    correlation_id: str,
    event_type: str,
    payload_hash: str,
    signature_valid: bool,
    server_id: Optional[str],
    action_taken: str
) -> None:
    now = get_utc_now_iso()
    result = ws_query("SELECT COALESCE(MAX(id), 0) + 1 as next_id FROM snow_webhook_log")
    next_id = result.get("rows", [{}])[0].get("next_id", 1)
    ws_write("snow_webhook_log", [{
        "id": next_id,
        "webhook_id": webhook_id,
        "snow_ticket_number": snow_ticket_number,
        "correlation_id": correlation_id,
        "event_type": event_type,
        "payload_hash": payload_hash,
        "signature_valid": signature_valid,
        "server_id": server_id,
        "processed": True,
        "action_taken": action_taken,
        "created_at": now
    }])


def is_duplicate_webhook(webhook_id: str) -> bool:
    result = ws_query(
        f"SELECT COUNT(*) as cnt FROM snow_webhook_log WHERE webhook_id = '{webhook_id}'"
    )
    count = result.get("rows", [{}])[0].get("cnt", 0)
    return count > 0


def get_stored_token() -> Optional[Dict[str, Any]]:
    result = ws_query("SELECT * FROM snow_token_cache WHERE token_key = 'snow_oauth' LIMIT 1")
    rows = result.get("rows", [])
    if not rows:
        return None
    return rows[0]


def cache_token(access_token: str, expires_at: str) -> None:
    now = get_utc_now_iso()
    ws_execute("DELETE FROM snow_token_cache WHERE token_key = 'snow_oauth'")
    ws_write("snow_token_cache", [{
        "token_key": "snow_oauth",
        "access_token": access_token,
        "expires_at": expires_at,
        "refreshed_at": now
    }])


def get_snow_oauth_token() -> Optional[str]:
    cached = get_stored_token()
    if cached:
        expires_at_str = cached.get("expires_at", "")
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if expires_at > datetime.now(timezone.utc):
                log("Using cached OAuth token")
                return cached.get("access_token")
        except Exception as e:
            log(f"Token expiry parse error: {e}")
    return refresh_snow_oauth_token()


def refresh_snow_oauth_token() -> Optional[str]:
    log("Refreshing ServiceNow OAuth token")
    try:
        import os
        client_id = os.environ.get(SNOW_CLIENT_ID)
        client_secret = os.environ.get(SNOW_CLIENT_SECRET)
        instance_url = os.environ.get(SNOW_INSTANCE_URL)
        refresh_token = os.environ.get(SNOW_REFRESH_TOKEN)
        if not all([client_id, client_secret, instance_url, refresh_token]):
            log("Missing SNOW OAuth configuration, using fallback")
            return None
        token_url = f"{instance_url}/oauth_token.do"
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token
        }
        resp = requests.post(token_url, data=data, timeout=30)
        resp.raise_for_status()
        token_data = resp.json()
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", TOKEN_EXPIRY_SECONDS)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        expires_at_str = expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        cache_token(access_token, expires_at_str)
        log(f"Token refreshed, expires at {expires_at_str}")
        return access_token
    except Exception as e:
        log(f"Token refresh failed: {e}")
        return None


def resolve_ticket_in_snow(
    ticket_number: str,
    correlation_id: str,
    resolution_notes: str
) -> Optional[Dict[str, Any]]:
    log(f"Resolving SNOW ticket {ticket_number}")
    access_token = get_snow_oauth_token()
    if not access_token:
        log("No OAuth token available for ticket resolution")
        return None
    try:
        import os
        instance_url = os.environ.get(SNOW_INSTANCE_URL)
        update_url = f"{instance_url}/api/now/table/incident/{ticket_number}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "state": "7",
            "close_notes": resolution_notes,
            "correlation_id": correlation_id
        }
        resp = requests.patch(update_url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        log(f"Ticket {ticket_number} resolved successfully")
        return resp.json()
    except Exception as e:
        log(f"Ticket resolution failed: {e}")
        return None


def get_ticket_state_from_snow(ticket_number: str) -> Optional[str]:
    access_token = get_snow_oauth_token()
    if not access_token:
        return None
    try:
        import os
        instance_url = os.environ.get(SNOW_INSTANCE_URL)
        query_url = f"{instance_url}/api/now/table/incident"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"sysparm_query": f"number={ticket_number}", "sysparm_fields": "state,correlation_id"}
        resp = requests.get(query_url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("result", [])
        if records:
            return records[0].get("state")
    except Exception as e:
        log(f"Failed to get ticket state: {e}")
    return None


def find_server_by_ticket_correlation(correlation_id: str) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT * FROM mcp_server_registry
    WHERE server_id IN (
        SELECT target_server_id FROM audit_log
        WHERE event_type = 'snow_ticket_created'
        AND detail LIKE '%{correlation_id}%'
        ORDER BY created_at DESC
        LIMIT 1
    )
    LIMIT 1
    """
    result = ws_query(sql)
    rows = result.get("rows", [])
    return rows[0] if rows else None


def find_server_by_mcp_name(mcp_name: str) -> Optional[Dict[str, Any]]:
    sql = f"SELECT * FROM mcp_server_registry WHERE name = '{mcp_name}' LIMIT 1"
    result = ws_query(sql)
    rows = result.get("rows", [])
    return rows[0] if rows else None


class SnowWebhookPayload(BaseModel):
    ticket_number: str
    correlation_id: str
    event_type: str
    mcp_name: Optional[str] = None
    server_id: Optional[str] = None
    status: Optional[str] = None
    approval_result: Optional[str] = None
    notes: Optional[str] = None
    timestamp: Optional[str] = None


def parse_snow_webhook_payload(data: Dict[str, Any]) -> SnowWebhookPayload:
    return SnowWebhookPayload(
        ticket_number=data.get("ticket_number", data.get("number", "")),
        correlation_id=data.get("correlation_id", data.get("sys_id", "")),
        event_type=data.get("event_type", data.get("type", "unknown")),
        mcp_name=data.get("mcp_name", data.get("short_description", "")),
        server_id=data.get("server_id"),
        status=data.get("status", data.get("state", "")),
        approval_result=data.get("approval_result"),
        notes=data.get("notes", data.get("work_notes", "")),
        timestamp=data.get("timestamp", data.get("sys_updated_on", get_utc_now_iso()))
    )


async def handle_snow_webhook(
    request: Request,
    x_snow_signature: Optional[str] = Header(None),
    x_snow_webhook_id: Optional[str] = Header(None),
    x_snow_correlation_id: Optional[str] = Header(None)
) -> Dict[str, Any]:
    raw_body = await request.body()
    import os
    webhook_secret = os.environ.get(SNOW_WEBHOOK_SECRET, "")
    if not validate_webhook_signature(raw_body, x_snow_signature or "", webhook_secret):
        record_audit_log(
            event_type="snow_webhook_rejected",
            actor="snow_connector_completeness",
            detail={"reason": "invalid_signature", "webhook_id": x_snow_webhook_id}
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature"
        )
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as e:
        log(f"Invalid JSON payload: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")
    webhook_id = x_snow_webhook_id or payload.get("webhook_id", str(uuid.uuid4()))
    if is_duplicate_webhook(webhook_id):
        log(f"Duplicate webhook {webhook_id}, skipping")
        return {"status": "duplicate", "webhook_id": webhook_id}
    parsed = parse_snow_webhook_payload(payload)
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    action_taken = "none"
    server_record = None
    if parsed.server_id:
        sql = f"SELECT * FROM mcp_server_registry WHERE server_id = '{parsed.server_id}' LIMIT 1"
        result = ws_query(sql)
        server_record = result.get("rows", [{}])[0] if result.get("rows") else None
    elif parsed.correlation_id:
        server_record = find_server_by_ticket_correlation(parsed.correlation_id)
    elif parsed.mcp_name:
        server_record = find_server_by_mcp_name(parsed.mcp_name)
    record_audit_log(
        event_type="snow_webhook_received",
        actor="snow_connector_completeness",
        detail={
            "webhook_id": webhook_id,
            "ticket_number": parsed.ticket_number,
            "correlation_id": parsed.correlation_id,
            "event_type": parsed.event_type,
            "server_id": server_record.get("server_id") if server_record else None,
            "mcp_name": parsed.mcp_name
        },
        target_server_id=server_record.get("server_id") if server_record else None
    )
    if parsed.event_type in ["approval_completed", "approval_updated"]:
        if parsed.approval_result == "approved":
            log(f"MUST NOT auto-approve: Ticket {parsed.ticket_number} approval requires analyst review")
            record_audit_log(
                event_type="snow_auto_approve_blocked",
                actor="snow_connector_completeness",
                detail={
                    "ticket_number": parsed.ticket_number,
                    "correlation_id": parsed.correlation_id,
                    "reason": "analyst_review_required"
                },
                target_server_id=server_record.get("server_id") if server_record else None
            )
            action_taken = "blocked_awaiting_analyst_review"
        elif parsed.approval_result == "rejected":
            log(f"Recording rejection from SNOW for ticket {parsed.ticket_number}")
            action_taken = "rejection_recorded"
            record_audit_log(
                event_type="snow_approval_rejected",
                actor="snow_connector_completeness",
                detail={
                    "ticket_number": parsed.ticket_number,
                    "correlation_id": parsed.correlation_id,
                    "notes": parsed.notes
                },
                target_server_id=server_record.get("server_id") if server_record else None
            )
    elif parsed.event_type == "ticket_resolved":
        log(f"Ticket {parsed.ticket_number} resolved externally")
        action_taken = "ticket_resolved_notification"
    elif parsed.event_type == "ticket_updated":
        log(f"Ticket {parsed.ticket_number} updated")
        action_taken = "ticket_update_recorded"
    record_snow_webhook_log(
        webhook_id=webhook_id,
        snow_ticket_number=parsed.ticket_number,
        correlation_id=parsed.correlation_id,
        event_type=parsed.event_type,
        payload_hash=payload_hash,
        signature_valid=True,
        server_id=server_record.get("server_id") if server_record else None,
        action_taken=action_taken
    )
    return {
        "status": "processed",
        "webhook_id": webhook_id,
        "correlation_id": parsed.correlation_id,
        "ticket_number": parsed.ticket_number,
        "action_taken": action_taken,
        "server_id": server_record.get("server_id") if server_record else None
    }


@app.post("/webhook/snow")
async def snow_webhook_endpoint(request: Request) -> Dict[str, Any]:
    return await handle_snow_webhook(request)


@app.post("/snow/callback")
async def snow_callback_endpoint(request: Request) -> Dict[str, Any]:
    return await handle_snow_webhook(request)


@app.get("/health")
async def health() -> Dict[str, Any]:
    uptime = int(time.time() - _start_time)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime_seconds": uptime
    }


@app.get("/status")
async def status_endpoint() -> Dict[str, Any]:
    token_status = "unknown"
    cached = get_stored_token()
    if cached:
        expires_at_str = cached.get("expires_at", "")
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            token_status = "valid" if expires_at > datetime.now(timezone.utc) else "expired"
        except Exception:
            token_status = "parse_error"
    result = ws_query("SELECT COUNT(*) as cnt FROM snow_webhook_log WHERE processed = TRUE")
    processed_count = result.get("rows", [{}])[0].get("cnt", 0)
    return {
        "service": SERVICE_NAME,
        "token_status": token_status,
        "webhooks_processed": processed_count,
        "uptime_seconds": int(time.time() - _start_time)
    }


@app.post("/token/refresh")
async def refresh_token_endpoint() -> Dict[str, Any]:
    new_token = refresh_snow_oauth_token()
    if new_token:
        return {"status": "refreshed", "token_length": len(new_token)}
    return {"status": "refresh_failed"}


def create_tables() -> None:
    ensure_audit_table()
    ensure_snow_webhook_log_table()
    ensure_snow_token_cache_table()
    log("All required tables verified/created")


def run() -> None:
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    if not check_single_instance():
        return
    log(f"Starting {SERVICE_NAME} on port {PORT}")
    create_tables()
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    run()