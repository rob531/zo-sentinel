#!/usr/bin/env python3
"""
ServiceNow Inbound Webhook Handler for ZO-SENTINEL
Phase 9: ServiceNow integration for MCP request tickets
Receives inbound webhooks only - no outbound calls
"""

import os
import sys
import time
import json
import hmac
import hashlib
import signal
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import uvicorn
import requests
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse

SERVICE_NAME = "snow_inbound_webhook"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")
SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")
SNOW_OAUTH_TOKEN = os.environ.get("SNOW_OAUTH_TOKEN", "")
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
HEARTBEAT_INTERVAL = 60

app = FastAPI()
start_time = time.time()

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def check_single_instance() -> bool:
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log(f"Instance already running PID={old_pid}")
                return False
            except OSError:
                pass
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log(f"check_single_instance error: {e}")
        return False

def remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(signum, frame):
    log(f"Received signal {signum}")
    remove_pid_file()
    sys.exit(0)

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_query error: {e}")
        return {"rows": [], "count": 0}

def ws_write(table: str, data: Dict[str, Any]) -> bool:
    try:
        payload = {"table": table, "rows": data}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_write error ({table}): {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_execute error: {e}")
        return False

def send_heartbeat() -> None:
    try:
        payload = {
            "table": "service_health",
            "rows": {"service": SERVICE_NAME, "last_heartbeat": datetime.now(timezone.utc).isoformat()}
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except Exception as e:
        log(f"Heartbeat error: {e}")

def validate_snow_oauth_token(token: str) -> bool:
    if not token or not SNOW_INSTANCE:
        return False
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = requests.get(
            f"{SNOW_INSTANCE}/api/now/table/sys_user",
            headers=headers,
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log(f"OAuth validation error: {e}")
        return False

def validate_snow_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    try:
        expected = hmac.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)
    except Exception as e:
        log(f"Signature validation error: {e}")
        return False

def extract_ticket_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    short_desc = payload.get("short_description", "")
    caller_id = payload.get("caller_id", "unknown")
    if isinstance(caller_id, dict):
        caller_id = caller_id.get("value", caller_id.get("display_value", "unknown"))
    sys_id = payload.get("sys_id", "")
    description = payload.get("description", "")
    created_on = payload.get("sys_created_on", datetime.now(timezone.utc).isoformat())
    number = payload.get("number", "")
    state = payload.get("state", "")
    
    return {
        "mcp_name": short_desc,
        "requested_by": caller_id,
        "request_timestamp": created_on,
        "ticket_id": sys_id,
        "ticket_number": number,
        "description": description,
        "state": state
    }

def write_audit_log(event_type: str, detail: str, target_server_id: Optional[str] = None) -> None:
    entry = {
        "event_type": event_type,
        "actor": "service_now",
        "detail": detail,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    if target_server_id:
        entry["target_server_id"] = target_server_id
    ws_write("audit_log", entry)

def ensure_tables() -> None:
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_submissions (
            submission_id VARCHAR PRIMARY KEY,
            mcp_name VARCHAR,
            requested_by VARCHAR,
            request_timestamp TIMESTAMP,
            ticket_id VARCHAR,
            ticket_number VARCHAR,
            description TEXT,
            state VARCHAR,
            source VARCHAR DEFAULT 'service_now',
            status VARCHAR DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    ws_execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id VARCHAR PRIMARY KEY,
            event_type VARCHAR,
            target_server_id VARCHAR,
            actor VARCHAR,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

@app.post("/webhook/snow")
async def receive_snow_webhook(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_snow_signature: Optional[str] = Header(None)
) -> JSONResponse:
    try:
        body = await request.body()
        
        oauth_token = None
        if authorization and authorization.startswith("Bearer "):
            oauth_token = authorization[7:]
        
        if not oauth_token:
            write_audit_log("snow_inbound_rejected", "Missing Authorization header", None)
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        
        if not validate_snow_oauth_token(oauth_token):
            write_audit_log("snow_inbound_rejected", "Invalid OAuth token", None)
            raise HTTPException(status_code=401, detail="Invalid OAuth token")
        
        if x_snow_signature and SNOW_WEBHOOK_SECRET:
            if not validate_snow_signature(body, x_snow_signature, SNOW_WEBHOOK_SECRET):
                write_audit_log("snow_inbound_rejected", "Invalid webhook signature", None)
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
        
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            write_audit_log("snow_inbound_error", f"JSON decode error: {str(e)}", None)
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
        
        ticket_data = extract_ticket_data(payload)
        
        submission_id = f"snow_{ticket_data.get('ticket_id', uuid.uuid4().hex[:8])}_{int(time.time())}"
        
        submission_record = {
            "submission_id": submission_id,
            "mcp_name": ticket_data.get("mcp_name", ""),
            "requested_by": ticket_data.get("requested_by", ""),
            "request_timestamp": ticket_data.get("request_timestamp", datetime.now(timezone.utc).isoformat()),
            "ticket_id": ticket_data.get("ticket_id", ""),
            "ticket_number": ticket_data.get("ticket_number", ""),
            "description": ticket_data.get("description", ""),
            "state": ticket_data.get("state", ""),
            "source": "service_now",
            "status": "pending"
        }
        
        if not ws_write("mcp_submissions", submission_record):
            write_audit_log(
                "snow_inbound_db_error",
                f"Failed to write submission: {submission_id}",
                None
            )
            raise HTTPException(status_code=500, detail="Database write failed")
        
        write_audit_log(
            "snow_inbound_received",
            f"MCP request ticket received: submission_id={submission_id}, mcp_name={ticket_data.get('mcp_name', 'unknown')}, requested_by={ticket_data.get('requested_by', 'unknown')}",
            None
        )
        
        log(f"Inbound webhook processed: {submission_id}")
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "accepted",
                "submission_id": submission_id,
                "message": "Webhook received and processed"
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        log(f"Webhook processing error: {e}")
        write_audit_log("snow_inbound_error", f"Processing error: {str(e)}", None)
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/health")
async def health() -> JSONResponse:
    uptime = int(time.time() - start_time)
    return JSONResponse(content={"status": "ok", "service": SERVICE_NAME, "uptime": uptime})

@app.get("/status")
async def status() -> JSONResponse:
    uptime = int(time.time() - start_time)
    result = ws_query("SELECT COUNT(*) as count FROM mcp_submissions WHERE source = 'service_now'")
    snow_submissions = 0
    if result.get("rows"):
        snow_submissions = result["rows"][0].get("count", 0)
    
    return JSONResponse(content={
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime": uptime,
        "snow_submissions": snow_submissions,
        "snow_instance": SNOW_INSTANCE or "not_configured"
    })

def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def run() -> None:
    log(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log("Cannot start - another instance running")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    
    import threading
    hb = threading.Thread(target=heartbeat_loop, daemon=True)
    hb.start()
    
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")

if __name__ == "__main__":
    run()