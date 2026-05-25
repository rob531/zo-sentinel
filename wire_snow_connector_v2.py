#!/usr/bin/env python3
"""
Wire integration: ServiceNow webhook -> mcp_submissions via write_service
Receives MCP request tickets from ServiceNow and persists to DB for approval_workflow.
"""
import sys
import time
import json
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
import uvicorn

SERVICE_NAME = "wire_snow_connector_v2"
PORT = 8783  # Use 8783 for wire_snow_connector (8772 is write_service, 8773 is inference_router)

# Write service endpoint
WRITE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"

app = FastAPI()


def check_instance():
    """Ensure single instance via PID file."""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    try:
        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())
            if pid > 0:
                try:
                    import os
                    os.kill(pid, 0)
                    print(f"[ERROR] Another instance running with PID {pid}")
                    sys.exit(1)
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))


def send_heartbeat():
    """Send service heartbeat to write_service."""
    try:
        requests.post(WRITE_URL, json={
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }, timeout=5)
    except Exception as e:
        print(f"[WARN] Heartbeat failed: {e}")


def write_to_db(table, rows):
    """Write rows to DB via write_service."""
    try:
        resp = requests.post(WRITE_URL, json={
            "table": table,
            "rows": rows,
            "wait": True
        }, timeout=10)
        return resp.json() if resp.ok else None
    except Exception as e:
        print(f"[ERROR] DB write failed: {e}")
        return None


def query_db(sql):
    """Query DB via write_service."""
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        return resp.json() if resp.ok else None
    except Exception as e:
        print(f"[ERROR] DB query failed: {e}")
        return None


def ensure_tables():
    """Ensure mcp_submissions table exists."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_submissions (
        submission_id VARCHAR PRIMARY KEY,
        requester_email VARCHAR,
        mcp_name VARCHAR,
        mcp_url VARCHAR,
        description TEXT,
        justification TEXT,
        ticket_number VARCHAR,
        service_now_link VARCHAR,
        status VARCHAR DEFAULT 'pending',
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    try:
        requests.post("http://127.0.0.1:8772/execute", json={"sql": create_sql}, timeout=10)
    except Exception as e:
        print(f"[WARN] Table creation: {e}")


def ensure_approval_queue():
    """Ensure approval_workflow can read submissions - ensure related table exists."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS approval_queue (
        queue_id VARCHAR PRIMARY KEY,
        submission_id VARCHAR,
        status VARCHAR DEFAULT 'queued',
        priority INTEGER DEFAULT 0,
        assigned_reviewer VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (submission_id) REFERENCES mcp_submissions(submission_id)
    )
    """
    try:
        requests.post("http://127.0.0.1:8772/execute", json={"sql": create_sql}, timeout=10)
    except Exception as e:
        print(f"[WARN] Approval queue table: {e}")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME, "uptime": getattr(app.state, "uptime", 0)}


@app.post("/webhook/servicenow")
async def servicenow_webhook(request: Request):
    """
    Receive MCP request tickets from ServiceNow.
    Expected payload format:
    {
        "ticket_number": "INC0012345",
        "requester_email": "user@company.com",
        "mcp_name": "mcp-server-xyz",
        "mcp_url": "https://registry.npmjs.org/mcp-server-xyz",
        "description": "Use case description",
        "justification": "Business justification",
        "service_now_link": "https://company.service-now.com/nav_to.do?uri=incident.do?sys_id=xxx"
    }
    """
    try:
        body = await request.json()
        ticket_number = body.get("ticket_number")
        
        if not ticket_number:
            # Try to extract from ServiceNow payload format
            ticket_number = body.get("number") or body.get("sys_id") or body.get("incident_id")
        
        if not ticket_number:
            raise HTTPException(status_code=400, detail="Missing ticket_number")
        
        # Generate submission ID
        submission_id = f"snow_{ticket_number}_{int(time.time())}"
        
        # Prepare submission record
        submission = {
            "submission_id": submission_id,
            "requester_email": body.get("requester_email") or body.get("caller_id") or "unknown@service-now.local",
            "mcp_name": body.get("mcp_name") or body.get("short_description") or "unknown-mcp",
            "mcp_url": body.get("mcp_url") or body.get("mcp_registry_url") or "",
            "description": body.get("description") or body.get("work_notes") or body.get("comments") or "",
            "justification": body.get("justification") or body.get("business Justification") or "",
            "ticket_number": str(ticket_number),
            "service_now_link": body.get("service_now_link") or f"https://company.service-now.com/nav_to.do?uri=incident.do?sys_id={ticket_number}",
            "status": "pending",
            "submitted_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        # Write to mcp_submissions
        result = write_to_db("mcp_submissions", submission)
        
        if result:
            # Also queue for approval_workflow
            queue_entry = {
                "queue_id": f"q_{submission_id}",
                "submission_id": submission_id,
                "status": "queued",
                "priority": body.get("priority", 5),
                "assigned_reviewer": body.get("assigned_to") or "",
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            write_to_db("approval_queue", queue_entry)
            
            return {
                "status": "accepted",
                "submission_id": submission_id,
                "queued": True
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to persist submission")
            
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/submissions/{submission_id}")
async def get_submission(submission_id: str):
    """Retrieve submission details for approval_workflow."""
    result = query_db(f"SELECT * FROM mcp_submissions WHERE submission_id = '{submission_id}'")
    if result and result.get("rows"):
        return {"status": "found", "submission": result["rows"][0]}
    return {"status": "not_found"}


@app.get("/submissions")
async def list_submissions(status: str = None, limit: int = 50):
    """List submissions, optionally filtered by status."""
    if status:
        result = query_db(f"SELECT * FROM mcp_submissions WHERE status = '{status}' ORDER BY submitted_at DESC LIMIT {limit}")
    else:
        result = query_db(f"SELECT * FROM mcp_submissions ORDER BY submitted_at DESC LIMIT {limit}")
    
    if result:
        return {"status": "ok", "count": result.get("count", 0), "submissions": result.get("rows", [])}
    return {"status": "ok", "count": 0, "submissions": []}


@app.put("/submissions/{submission_id}/status")
async def update_status(submission_id: str, new_status: str):
    """Update submission status (for approval_workflow to call)."""
    result = write_to_db("mcp_submissions", {
        "submission_id": submission_id,
        "status": new_status,
        "updated_at": datetime.utcnow().isoformat()
    })
    if result:
        return {"status": "updated", "submission_id": submission_id, "new_status": new_status}
    raise HTTPException(status_code=500, detail="Update failed")


@app.on_event("startup")
async def startup():
    """Initialize on startup."""
    import os
    check_instance()
    app.state.start_time = time.time()
    ensure_tables()
    ensure_approval_queue()
    send_heartbeat()


def run():
    """Start the wire service."""
    import os
    print(f"[INFO] Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    run()