import os
import sys
import time
import json
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

import requests

SERVICE_NAME = "snow_connector_approval_integration"
SERVICE_PORT = 8791
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
POLL_SECS = 30
HEARTBEAT_INTERVAL = 60

_start_time = None
_stop_event = False


def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ws_query(sql: str, params: Optional[List] = None) -> Dict[str, Any]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {"table": table, "rows": rows}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str, params: Optional[List] = None) -> Dict[str, Any]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_single_instance() -> bool:
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Another instance already running with PID {old_pid}")
            return False
        except OSError:
            log(f"Stale PID file found, removing")
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    log(f"Running as PID {pid}")
    return True


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    global _stop_event
    log(f"Received signal {signum}, shutting down")
    _stop_event = True
    remove_pid_file()
    sys.exit(0)


def get_utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_uptime_seconds() -> float:
    if _start_time is None:
        return 0.0
    return (datetime.now(timezone.utc) - _start_time).total_seconds()


def send_heartbeat():
    now = get_utc_now_iso()
    rows = [{"service": SERVICE_NAME, "last_heartbeat": now}]
    try:
        ws_write("service_health", rows)
    except Exception as e:
        log(f"Heartbeat failed: {e}")


def ensure_tables():
    log("Ensuring integration tables exist")
    tables_sql = [
        """CREATE TABLE IF NOT EXISTS snow_approval_status (
            id INTEGER DEFAULT nextid('snow_approval_status_seq') PRIMARY KEY,
            submission_id VARCHAR,
            snow_ticket_id VARCHAR,
            snow_state VARCHAR,
            snow_state_updated_at VARCHAR,
            last_checked_at VARCHAR,
            processed BOOLEAN DEFAULT false
        )""",
        """CREATE SEQUENCE IF NOT EXISTS snow_approval_status_seq"""
    ]
    for sql in tables_sql:
        try:
            ws_execute(sql)
        except Exception as e:
            log(f"Table creation note: {e}")


def get_pending_snow_resolutions() -> List[Dict[str, Any]]:
    """Submissions parked awaiting a ServiceNow resolution.

    `approval_workflow` IS NOT A TABLE. approval_workflow.py is the approval
    SERVICE (port 8780, see app_routes.py); a module name was read as a table
    name. That service writes `mcp_submissions` and `mcp_decisions` -- the
    submission row, with its own `status` and `submitted_at`, is the referent
    this query wanted. It also joined `mcp_server_registry` on `submission_id`,
    a column that table does not have; the submission carries both the id and
    the server. `review_token` existed on no table and was never read. Refs #4080.
    """
    sql = """
    SELECT
        s.server_id,
        s.mcp_name AS name,
        s.submission_id,
        s.status AS approval_status,
        sa.snow_ticket_id,
        sa.snow_state,
        sa.snow_state_updated_at
    FROM mcp_submissions s
    LEFT JOIN snow_approval_status sa ON s.submission_id = sa.submission_id
    WHERE s.status IN ('pending_snow', 'awaiting_snow_resolution')
    ORDER BY s.submitted_at ASC
    """
    try:
        result = ws_query(sql)
        return result.get("rows", [])
    except Exception as e:
        log(f"Failed to fetch pending SNOW resolutions: {e}")
        return []


def get_snow_ticket_state(ticket_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT 
        snow_ticket_id,
        snow_state,
        snow_state_updated_at
    FROM snow_approval_status
    WHERE snow_ticket_id = ?
    ORDER BY id DESC
    LIMIT 1
    """
    try:
        result = ws_query(sql, [ticket_id])
        rows = result.get("rows", [])
        if rows:
            return rows[0]
    except Exception as e:
        log(f"Failed to fetch SNOW ticket state for {ticket_id}: {e}")
    return None


def update_approval_workflow_state(submission_id: str, new_status: str, snow_ticket_id: str, detail: str):
    now = get_utc_now_iso()
    # The submission's own status is the approval state. See the note on
    # get_pending_snow_resolutions: there is no `approval_workflow` table, and
    # mcp_submissions has no updated_at column -- the timestamp lives on the
    # snow_approval_status row this module owns and records below.
    sql = """
    UPDATE mcp_submissions
    SET status = ?
    WHERE submission_id = ?
    """
    try:
        ws_execute(sql, [new_status, submission_id])
        log(f"Updated approval_workflow for {submission_id} to status={new_status}")
        
        audit_rows = [{
            "target_server_id": submission_id,
            "event_type": f"snow_ticket_{new_status}",
            "actor": SERVICE_NAME,
            "detail": detail,
            "created_at": now
        }]
        ws_write("audit_log", audit_rows)
        log(f"Audit log entry recorded for {submission_id}")
    except Exception as e:
        log(f"Failed to update approval_workflow: {e}")


def record_snow_check(submission_id: str, ticket_id: str, state: str, state_updated_at: str):
    rows = [{
        "submission_id": submission_id,
        "snow_ticket_id": ticket_id,
        "snow_state": state,
        "snow_state_updated_at": state_updated_at,
        "last_checked_at": get_utc_now_iso(),
        "processed": False
    }]
    try:
        ws_write("snow_approval_status", rows)
    except Exception as e:
        log(f"Failed to record SNOW check: {e}")


def map_snow_state_to_approval(snow_state: str) -> Optional[str]:
    state_map = {
        "resolved": "approved",
        "closed": "approved",
        "completed": "approved",
        "approved": "approved",
        "rejected": "rejected",
        "cancelled": "cancelled",
        "pending": "pending_snow",
        "in_progress": "awaiting_snow_resolution"
    }
    return state_map.get(snow_state.lower(), None)


def process_snow_resolution(pending: Dict[str, Any]):
    server_id = pending.get("server_id", "unknown")
    submission_id = pending.get("submission_id", "")
    ticket_id = pending.get("snow_ticket_id")
    
    log(f"Processing SNOW resolution for submission_id={submission_id}, ticket_id={ticket_id}")
    
    if not ticket_id:
        log(f"No SNOW ticket_id for submission {submission_id}, skipping")
        return
    
    ticket_state = get_snow_ticket_state(ticket_id)
    
    if not ticket_state:
        log(f"No SNOW ticket record for {ticket_id}, creating initial record")
        record_snow_check(submission_id, ticket_id, "pending", get_utc_now_iso())
        return
    
    current_state = ticket_state.get("snow_state", "")
    state_updated_at = ticket_state.get("snow_state_updated_at", "")
    
    approval_mapping = map_snow_state_to_approval(current_state)
    
    if approval_mapping and approval_mapping not in ["pending_snow", "awaiting_snow_resolution"]:
        detail = f"SNOW ticket {ticket_id} transitioned to state '{current_state}' at {state_updated_at}"
        update_approval_workflow_state(submission_id, approval_mapping, ticket_id, detail)
        
        mark_sql = "UPDATE snow_approval_status SET processed = true WHERE submission_id = ?"
        try:
            ws_execute(mark_sql, [submission_id])
        except Exception as e:
            log(f"Failed to mark as processed: {e}")
        
        log(f"Approval state updated for {submission_id}: {approval_mapping}")
    else:
        log(f"SNOW ticket {ticket_id} still in state '{current_state}', waiting")


def check_for_new_snow_tickets():
    """UNRESOLVED REFERENT -- there is no source for an untracked ticket id.

    This asked `approval_workflow` (not a table -- see
    get_pending_snow_resolutions) for a `snow_ticket_id` on submissions that,
    by its own NOT EXISTS clause, have no row in `snow_approval_status`. But
    `snow_approval_status` -- created by ensure_tables() in this very module --
    is the ONLY place on any plane where a snow_ticket_id is stored. The query
    asks for a ticket id from everywhere it is not.

    That is not a naming defect and no rename repairs it. Unlike the other two
    statements in this file, whose intended referent is recoverable from
    approval_workflow.py's own writes, this one has no assignment of real
    tables that makes it true.

    It has therefore ALWAYS returned [] -- ws_query raised on the missing
    table and the handler swallowed it -- so returning [] here changes nothing
    that ever happened. What it does change is that the module no longer names
    a table that does not exist.

    TO REBUILD: the SNOW connector needs a ticket id recorded at the moment the
    ticket is opened, on a table that is not the one used to decide the ticket
    is untracked. Until that exists, this discovery path cannot work. Refs #4080.
    """
    return []


def discover_and_track_new_tickets():
    new_tickets = check_for_new_snow_tickets()
    for entry in new_tickets:
        submission_id = entry.get("submission_id")
        ticket_id = entry.get("snow_ticket_id")
        if submission_id and ticket_id:
            log(f"Discovered new SNOW ticket {ticket_id} for submission {submission_id}")
            record_snow_check(submission_id, ticket_id, "pending", get_utc_now_iso())


def heartbeat_loop():
    last_heartbeat = time.time()
    while not _stop_event:
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_heartbeat()
            last_heartbeat = now
        time.sleep(5)


def run():
    global _start_time
    _start_time = datetime.now(timezone.utc)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        sys.exit(1)
    
    try:
        ensure_tables()
    except Exception as e:
        log(f"Failed to ensure tables: {e}")
    
    log(f"Starting SNOW approval integration loop (poll every {POLL_SECS}s)")
    
    cycle_count = 0
    last_heartbeat = time.time()
    
    while not _stop_event:
        try:
            cycle_start = time.time()
            
            discover_and_track_new_tickets()
            
            pending_resolutions = get_pending_snow_resolutions()
            log(f"Found {len(pending_resolutions)} pending SNOW resolutions")
            
            for pending in pending_resolutions:
                try:
                    process_snow_resolution(pending)
                except Exception as e:
                    log(f"Error processing resolution: {e}")
            
            cycle_count += 1
            
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = now
                log(f"Cycle {cycle_count} completed, heartbeat sent")
            
            elapsed = time.time() - cycle_start
            sleep_time = max(1, POLL_SECS - elapsed)
            time.sleep(sleep_time)
            
        except Exception as e:
            log(f"Cycle error: {e}")
            time.sleep(POLL_SECS)
    
    remove_pid_file()
    log(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    run()