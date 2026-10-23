import os
import sys
import json
import logging
import time
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Header, Query
import uvicorn
import requests

# === CONSTANTS ===
SERVICE_NAME = "manual_override_api"
SERVICE_PORT = 8776
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
PID_FILE = "/tmp/manual_override_api_v2.pid"
LOG_FILE = "/home/workspace/logs/manual_override_api_v2.log"

# === LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# === FASTAPI APP ===
app = FastAPI(title="Manual Override API v2")

# === WRITE SERVICE HELPERS ===
def ws_write(table: str, rows: list) -> bool:
    """Write to DuckDB via write_service."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for {table}: {e}")
        return False

def ws_query(sql: str) -> Optional[list]:
    """Query DuckDB via write_service."""
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=15
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return None

def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        response = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False

# === SINGLE INSTANCE GUARD ===
def check_single_instance() -> bool:
    """Check if another instance is running."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if old_pid > 0:
                try:
                    os.kill(old_pid, 0)
                    log.error(f"Another instance already running with PID {old_pid}")
                    return False
                except OSError:
                    log.info(f"Stale PID file found, clearing")
                    os.remove(PID_FILE)
        except (ValueError, IOError):
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
    return True

def write_pid():
    """Write current PID to file."""
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def remove_pid_file():
    """Remove PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

# === AUDIT LOG HELPER ===
def write_audit_log(server_id: str, event_type: str, actor: str, detail: str) -> bool:
    """Write an audit log entry for override operations."""
    now = datetime.now(timezone.utc).isoformat()
    return ws_write("audit_log", [{
        "server_id": server_id,
        "event_type": event_type,
        "actor": actor,
        "detail": detail,
        "created_at": now
    }])

# === HEALTH ENDPOINT ===
@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME, "timestamp": datetime.now(timezone.utc).isoformat()}

# === API ENDPOINTS ===

@app.post("/servers/{server_id}/override")
def override_server_verdict(
    server_id: str,
    new_verdict: str,
    reason: str,
    override_type: str,
    authorization: Optional[str] = Header(None)
):
    """
    Apply a manual verdict override to an MCP server.
    
    Args:
        server_id: The server ID to override
        new_verdict: The new verdict (TRUSTED, AMBER, UNTRUSTED, UNKNOWN)
        reason: Human-readable justification for the override
        override_type: Type of override (manual, emergency, appeal_approved)
    """
    # Validate authorization header
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Validate verdict
    valid_verdicts = ["TRUSTED", "AMBER", "UNTRUSTED", "UNKNOWN", "AMBER_UNVERIFIED", "HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"]
    if new_verdict not in valid_verdicts:
        raise HTTPException(status_code=400, detail=f"Invalid verdict. Must be one of: {valid_verdicts}")
    
    # Validate override_type
    valid_types = ["manual", "emergency", "appeal_approved", "compliance", "security_review"]
    if override_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid override_type. Must be one of: {valid_types}")
    
    # Extract actor from authorization
    actor = "unknown"
    if authorization.startswith("Bearer "):
        actor = authorization[7:]
    
    # Check if server exists
    existing = ws_query(f"SELECT server_id, name, verdict FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if not existing:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found in registry")
    
    old_verdict = existing[0].get("verdict", "UNKNOWN")
    
    # Update the server's verdict
    success = ws_execute(f"""
        UPDATE mcp_server_registry 
        SET verdict = '{new_verdict}',
            last_assessed = '{datetime.now(timezone.utc).isoformat()}'
        WHERE server_id = '{server_id}'
    """)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update server verdict")
    
    # Write audit log
    audit_detail = f"Override from '{old_verdict}' to '{new_verdict}'. Reason: {reason}. Type: {override_type}"
    write_audit_log(server_id, "verdict_override", actor, audit_detail)
    
    # Record in manual_override_metadata if table exists
    now = datetime.now(timezone.utc).isoformat()
    ws_write("manual_override_metadata", [{
        "server_id": server_id,
        "previous_verdict": old_verdict,
        "new_verdict": new_verdict,
        "override_reason": reason,
        "override_type": override_type,
        "override_by": actor,
        "overridden_at": now
    }])
    
    log.info(f"Override applied: server={server_id}, {old_verdict}->{new_verdict}, by={actor}")
    
    return {
        "status": "success",
        "server_id": server_id,
        "previous_verdict": old_verdict,
        "new_verdict": new_verdict,
        "override_type": override_type,
        "timestamp": now
    }

@app.get("/servers/{server_id}/override/history")
def get_override_history(
    server_id: str,
    authorization: Optional[str] = Header(None)
):
    """Get override history for a server."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    history = ws_query(f"""
        SELECT * FROM manual_override_metadata 
        WHERE server_id = '{server_id}'
        ORDER BY overridden_at DESC
        LIMIT 50
    """)
    
    return {
        "server_id": server_id,
        "history": history or []
    }

@app.post("/servers/{server_id}/override/revert")
def revert_override(
    server_id: str,
    reason: str,
    authorization: Optional[str] = Header(None)
):
    """Revert the most recent override for a server."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Get the most recent override
    recent = ws_query(f"""
        SELECT previous_verdict, override_by 
        FROM manual_override_metadata 
        WHERE server_id = '{server_id}'
        ORDER BY overridden_at DESC 
        LIMIT 1
    """)
    
    if not recent:
        raise HTTPException(status_code=404, detail=f"No override found for server {server_id}")
    
    old_verdict = recent[0].get("previous_verdict", "UNKNOWN")
    actor = authorization[7:] if authorization.startswith("Bearer ") else "unknown"
    
    # Revert the verdict
    success = ws_execute(f"""
        UPDATE mcp_server_registry 
        SET verdict = '{old_verdict}',
            last_assessed = '{datetime.now(timezone.utc).isoformat()}'
        WHERE server_id = '{server_id}'
    """)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to revert override")
    
    # Write audit log
    audit_detail = f"Reverted override, restored verdict to '{old_verdict}'. Reason: {reason}"
    write_audit_log(server_id, "verdict_revert", actor, audit_detail)
    
    return {
        "status": "success",
        "server_id": server_id,
        "reverted_to": old_verdict,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# === MAIN ENTRY POINT ===
def run():
    """Run the FastAPI service."""
    if not check_single_instance():
        log.error("Another instance is running. Exiting.")
        sys.exit(1)
    
    write_pid()
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")

if __name__ == "__main__":
    run()