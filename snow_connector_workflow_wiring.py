import sys
import time
import signal
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = "snow_connector_workflow_wiring"
SERVICE_PORT = 0
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
POLL_SECS = 30
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

import os
os.makedirs(LOG_DIR, exist_ok=True)


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
    import os
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != pid:
                try:
                    os.kill(old_pid, 0)
                    log(f"Instance already running with PID {old_pid}")
                    return False
                except OSError:
                    log(f"Stale PID file, overwriting with {pid}")
        except (ValueError, IOError):
            pass
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        return True
    except IOError as e:
        log(f"Failed to write PID file: {e}")
        return False


def remove_pid_file() -> None:
    import os
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame) -> None:
    log(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_SERVICE_URL


def ws_query(sql: str) -> List[Dict[str, Any]]:
    url = get_query_url()
    try:
        resp = requests.post(url, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        return []
    except requests.exceptions.RequestException as e:
        log(f"ws_query error: {e}")
        return []
    except ValueError as e:
        log(f"ws_query JSON parse error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    url = get_write_url()
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log(f"ws_write error: {e}")
        return False
    except ValueError as e:
        log(f"ws_write JSON error: {e}")
        return False


def ws_execute(sql: str) -> bool:
    url = get_execute_url()
    try:
        resp = requests.post(url, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log(f"ws_execute error: {e}")
        return False


def send_heartbeat() -> None:
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": now}])


def ensure_tables() -> None:
    log("Ensuring required tables exist")
    create_mcp_decisions_log = """
    CREATE TABLE IF NOT EXISTS mcp_decisions_export_log (
        log_id BIGINT AUTOINCREMENT PRIMARY KEY,
        decision_id BIGINT,
        snow_sys_id VARCHAR,
        snow_table VARCHAR,
        exported_at TIMESTAMP,
        export_status VARCHAR,
        error_message TEXT
    )
    """
    ws_execute(create_mcp_decisions_log)


def get_pending_decisions() -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        d.decision_id,
        d.server_id,
        d.name,
        d.decision,
        d.decided_by,
        d.decided_at,
        d.status,
        r.url,
        r.description,
        r.verdict,
        r.trust_score
    FROM mcp_decisions d
    LEFT JOIN mcp_server_registry r ON d.server_id = r.server_id
    WHERE d.status = 'PENDING'
    ORDER BY d.decided_at ASC
    LIMIT 50
    """
    return ws_query(sql)


def get_decision_detail(decision_id: int) -> Dict[str, Any]:
    sql = f"""
    SELECT 
        d.*,
        r.url,
        r.description,
        r.verdict,
        r.trust_score,
        ss.signal_name,
        ss.score as signal_score,
        ss.evidence
    FROM mcp_decisions d
    LEFT JOIN mcp_server_registry r ON d.server_id = r.server_id
    LEFT JOIN mcp_signal_scores ss ON d.server_id = ss.server_id
    WHERE d.decision_id = {decision_id}
    """
    rows = ws_query(sql)
    return rows[0] if rows else {}


def build_snow_change_request(decision: Dict[str, Any]) -> Dict[str, Any]:
    decision_type = decision.get("decision", "UNKNOWN")
    
    short_description = f"MCP Approval Decision: {decision.get('name', 'Unknown')} (ID: {decision.get('server_id', 'N/A')})"
    
    if decision_type == "APPROVED":
        state = 3
        approval_status = "Approved"
        work_notes = f"MCP server APPROVED by {decision.get('decided_by', 'System')}"
    elif decision_type == "REJECTED":
        state = -1
        approval_status = "Rejected"
        work_notes = f"MCP server REJECTED by {decision.get('decided_by', 'System')}"
    elif decision_type == "NEEDS_REVIEW":
        state = 1
        approval_status = "Needs Review"
        work_notes = f"MCP server flagged for manual review"
    else:
        state = 1
        approval_status = "Pending"
        work_notes = f"MCP decision pending export"
    
    description = decision.get("description", "")
    if description and len(description) > 500:
        description = description[:497] + "..."
    
    return {
        "short_description": short_description,
        "description": description,
        "state": state,
        "approval_status": approval_status,
        "work_notes": work_notes,
        "u_server_id": decision.get("server_id"),
        "u_verdict": decision.get("verdict"),
        "u_trust_score": decision.get("trust_score"),
        "u_decision_by": decision.get("decided_by"),
        "u_decision_date": decision.get("decided_at"),
        "u_server_url": decision.get("url"),
        "category": "MCP Server Approval",
        "u_decision_type": decision_type,
    }


def export_to_servicenow(decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        import snow_connector
        
        change_request = build_snow_change_request(decision)
        
        server_id = decision.get("server_id")
        name = decision.get("name", "Unknown")
        
        log(f"Exporting decision {decision.get('decision_id')} to ServiceNow: {name} -> {decision.get('decision')}")
        
        if hasattr(snow_connector, 'create_change_request'):
            result = snow_connector.create_change_request(change_request)
            log(f"ServiceNow create_change_request result: {result}")
            return result
        elif hasattr(snow_connector, 'create_incident'):
            result = snow_connector.create_incident(change_request)
            log(f"ServiceNow create_incident result: {result}")
            return result
        elif hasattr(snow_connector, 'make_snow_request'):
            result = snow_connector.make_snow_request('table', 'u_mcp_approvals', 'POST', change_request)
            log(f"ServiceNow make_snow_request result: {result}")
            return result
        else:
            log(f"WARNING: snow_connector has no recognized export method")
            return {"sys_id": "NO_EXPORT_METHOD", "success": False}
            
    except ImportError as e:
        log(f"Could not import snow_connector: {e}")
        return None
    except Exception as e:
        log(f"Error exporting to ServiceNow: {e}")
        return None


def mark_decision_exported(decision_id: int, snow_sys_id: Optional[str] = None, snow_table: str = "u_mcp_approvals") -> bool:
    now = datetime.now(timezone.utc).isoformat()
    
    sql = f"""
    UPDATE mcp_decisions 
    SET status = 'EXPORTED', 
        snow_export_at = '{now}',
        snow_sys_id = '{snow_sys_id or ''}',
        snow_table = '{snow_table}'
    WHERE decision_id = {decision_id}
    """
    success = ws_execute(sql)
    
    if success:
        log_entry = {
            "decision_id": decision_id,
            "snow_sys_id": snow_sys_id or "",
            "snow_table": snow_table,
            "exported_at": now,
            "export_status": "SUCCESS" if snow_sys_id else "PARTIAL",
            "error_message": ""
        }
        ws_write("mcp_decisions_export_log", [log_entry])
    
    return success


def mark_decision_export_failed(decision_id: int, error: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    
    sql = f"""
    UPDATE mcp_decisions 
    SET status = 'EXPORT_FAILED'
    WHERE decision_id = {decision_id}
    """
    success = ws_execute(sql)
    
    if success:
        log_entry = {
            "decision_id": decision_id,
            "snow_sys_id": "",
            "snow_table": "",
            "exported_at": now,
            "export_status": "FAILED",
            "error_message": error[:500] if error else "Unknown error"
        }
        ws_write("mcp_decisions_export_log", [log_entry])
    
    return success


def process_pending_decisions() -> Dict[str, int]:
    stats = {"processed": 0, "exported": 0, "failed": 0, "skipped": 0}
    
    pending = get_pending_decisions()
    stats["processed"] = len(pending)
    
    if not pending:
        log("No pending decisions to export")
        return stats
    
    log(f"Found {len(pending)} pending decisions to process")
    
    for decision in pending:
        decision_id = decision.get("decision_id")
        if not decision_id:
            stats["skipped"] += 1
            continue
        
        log(f"Processing decision_id={decision_id}: {decision.get('name')}")
        
        snow_result = export_to_servicenow(decision)
        
        if snow_result is None:
            log(f"Could not export to ServiceNow - snow_connector unavailable")
            stats["skipped"] += 1
            continue
        
        snow_sys_id = None
        if isinstance(snow_result, dict):
            snow_sys_id = snow_result.get("sys_id") or snow_result.get("result", {}).get("sys_id")
        
        if snow_sys_id and snow_sys_id != "NO_EXPORT_METHOD":
            if mark_decision_exported(decision_id, snow_sys_id):
                stats["exported"] += 1
                log(f"Successfully exported decision {decision_id} with sys_id={snow_sys_id}")
            else:
                stats["failed"] += 1
                log(f"Failed to mark decision {decision_id} as exported")
        else:
            error_msg = snow_result.get("error", "Export returned no sys_id") if isinstance(snow_result, dict) else "No result from ServiceNow"
            if mark_decision_export_failed(decision_id, error_msg):
                stats["failed"] += 1
                log(f"Export failed for decision {decision_id}: {error_msg}")
            else:
                stats["failed"] += 1
    
    return stats


def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(POLL_SECS)


def run() -> None:
    log(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log("Another instance is running, exiting")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    
    import threading
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    log("Heartbeat thread started")
    
    send_heartbeat()
    
    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            log(f"=== Cycle {cycle_count} ===")
            
            stats = process_pending_decisions()
            log(f"Cycle {cycle_count} complete: {stats}")
            
        except Exception as e:
            log(f"Error in cycle {cycle_count}: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()