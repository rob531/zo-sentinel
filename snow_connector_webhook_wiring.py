import logging
import os
import sys
import signal
import time
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

SERVICE_NAME = "snow_connector_webhook_wiring"
SERVICE_PORT = 8778
WRITE_SERVICE_URL = "http://localhost:8772"
PID_FILE = f"/home/workspace/zo_sentinel/{SERVICE_NAME}.pid"
POLL_SECS = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)

app = FastAPI()

_instance_checked = False

def check_single_instance():
    global _instance_checked
    if _instance_checked:
        return True
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Another instance running with PID {old_pid}")
            return False
        except OSError:
            logger.info(f"Stale PID file found, removing")
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    _instance_checked = True
    return True

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def ws_write(table: str, rows: list) -> bool:
    payload = {
        "table": table,
        "rows": rows,
        "wait": True
    }
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed for {table}: {e}")
        return False

def ws_query(sql: str, params: Optional[tuple] = None) -> Optional[list]:
    payload = {
        "sql": sql,
        "params": params if params else []
    }
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return None

def send_heartbeat(status: str = "running", meta: Optional[dict] = None):
    row = {
        "service_name": SERVICE_NAME,
        "status": status,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "meta": json.dumps(meta) if meta else "{}"
    }
    ws_write("service_health", [row])

def compute_deterministic_id(*fields) -> str:
    content = "|".join(str(f) for f in fields)
    return hashlib.sha256(content.encode()).hexdigest()[:32]

class SnowWebhookPayload(BaseModel):
    ticket_id: str
    event_type: str
    approval_status: Optional[str] = None
    assigned_to: Optional[str] = None
    priority: Optional[str] = None
    short_description: Optional[str] = None
    sys_id: Optional[str] = None
    timestamp: Optional[str] = None

@app.post("/webhook/snow/connector/completion")
async def handle_snow_completion(request: Request):
    try:
        body = await request.json()
        logger.info(f"Received ServiceNow completion webhook: {json.dumps(body)[:500]}")
        
        ticket_id = body.get("ticket_id", body.get("sys_id", "unknown"))
        event_type = body.get("event_type", "unknown")
        approval_status = body.get("approval_status", body.get("state", "unknown"))
        
        record_id = compute_deterministic_id(
            ticket_id,
            event_type,
            approval_status,
            datetime.now(timezone.utc).isoformat()
        )
        
        webhook_record = {
            "record_id": record_id,
            "source_system": "snow_connector",
            "ticket_id": ticket_id,
            "event_type": event_type,
            "approval_status": approval_status,
            "assigned_to": body.get("assigned_to", ""),
            "priority": body.get("priority", ""),
            "short_description": body.get("short_description", ""),
            "sys_id": body.get("sys_id", ""),
            "raw_payload": json.dumps(body),
            "received_at": datetime.now(timezone.utc).isoformat(),
            "processed": False,
            "routed_to_workflow": False
        }
        
        success = ws_write("snow_webhook_events", [webhook_record])
        
        if not success:
            logger.error("Failed to write webhook record to DuckDB")
            raise HTTPException(status_code=500, detail="Failed to persist webhook event")
        
        workflow_payload = {
            "source": "snow_connector_webhook",
            "ticket_id": ticket_id,
            "event_type": event_type,
            "approval_status": approval_status,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "assigned_to": body.get("assigned_to"),
                "priority": body.get("priority"),
                "short_description": body.get("short_description"),
                "sys_id": body.get("sys_id")
            }
        }
        
        workflow_record_id = compute_deterministic_id(
            "approval_workflow",
            ticket_id,
            event_type,
            datetime.now(timezone.utc).isoformat()
        )
        
        workflow_record = {
            "record_id": workflow_record_id,
            "workflow_name": "snow_connector_approval_flow",
            "source_event_id": record_id,
            "trigger_type": "snow_webhook",
            "ticket_id": ticket_id,
            "event_type": event_type,
            "approval_status": approval_status,
            "workflow_payload": json.dumps(workflow_payload),
            "status": "pending",
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "processed_at": None
        }
        
        workflow_success = ws_write("approval_workflow_queue", [workflow_record])
        
        if workflow_success:
            logger.info(f"Routed completion event to approval_workflow: ticket_id={ticket_id}, event_type={event_type}")
            update_payload = {"record_id": record_id, "routed_to_workflow": True}
            requests.post(f"{WRITE_SERVICE_URL}/update", json={"table": "snow_webhook_events", "row": update_payload}, timeout=10)
        else:
            logger.warning(f"Failed to route to approval_workflow, webhook persisted for retry")
        
        return {
            "status": "accepted",
            "record_id": record_id,
            "ticket_id": ticket_id,
            "routed": workflow_success
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing snow webhook: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {str(e)}")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": SERVICE_NAME}

@app.get("/status")
async def get_status():
    return {
        "service": SERVICE_NAME,
        "port": SERVICE_PORT,
        "status": "running"
    }

def cycle():
    logger.info("Snow connector webhook wiring cycle - monitoring for stale events")
    
    stale_query = """
    SELECT record_id, ticket_id, received_at 
    FROM snow_webhook_events 
    WHERE processed = false 
    AND routed_to_workflow = false 
    AND received_at < (CURRENT_TIMESTAMP - INTERVAL '1 hour')
    LIMIT 50
    """
    
    stale_events = ws_query(stale_query)
    
    if stale_events:
        logger.info(f"Found {len(stale_events)} stale unprocessed webhook events")
        for event in stale_events:
            logger.info(f"Stale event: record_id={event.get('record_id')}, ticket_id={event.get('ticket_id')}")
    else:
        logger.debug("No stale webhook events found")
    
    send_heartbeat("running", {"stale_events": len(stale_events) if stale_events else 0})

def run():
    if not check_single_instance():
        logger.error("Failed to acquire instance lock")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    send_heartbeat("started", {"port": SERVICE_PORT})
    
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")

if __name__ == "__main__":
    run()