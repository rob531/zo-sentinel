import os
import time
import logging
import signal
import requests
from datetime import datetime, timezone

SERVICE_NAME = "snow_connector_integration_wiring"
SERVICE_PORT = 0
PID_FILE = "/tmp/snow_connector_integration_wiring.pid"
LOG_FILE = "/home/workspace/logs/snow_connector_integration_wiring.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SNOW_CONNECTOR_URL = "http://127.0.0.1:8778"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 30
MAX_RETRIES = 3
BACKOFF_BASE = 5

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)


def ws_write(table: str, rows) -> bool:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        r = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_write {table}: {e}")
        return False


def ws_query(sql: str, limit: int = 100) -> list:
    try:
        r = requests.post(f"{QUERY_URL}", json={"sql": sql, "limit": limit}, timeout=10)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query: {e}")
    return []


def ws_execute(sql: str) -> bool:
    try:
        r = requests.post(f"{EXECUTE_URL}", json={"sql": sql}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_execute: {e}")
        return False


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance running with PID {old_pid}. Exiting.")
            return False
        except OSError:
            log.warning(f"Stale PID file found. Removing.")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}. Shutting down gracefully.")
    remove_pid_file()
    raise SystemExit(0)


def send_heartbeat(status: str = "running", meta: str = ""):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": status,
        "meta": meta
    }
    ws_write("service_health", row)


def get_pending_snow_submissions() -> list:
    sql = """
    SELECT 
        submission_id,
        server_name,
        url,
        description,
        submitted_by,
        submitted_at,
        snow_ticket_id,
        snow_ticket_sys_id,
        decision
    FROM mcp_submissions 
    WHERE decision IN ('APPROVED', 'CONDITIONAL')
      AND (snow_ticket_id IS NULL OR snow_ticket_id = '')
    LIMIT 20
    """
    return ws_query(sql)


def call_snow_connector_create_ticket(submission: dict, attempt: int = 1) -> dict:
    try:
        payload = {
            "short_description": f"MCP Approval: {submission.get('server_name', 'Unknown')}",
            "description": submission.get('description', ''),
            "u_mcp_server_name": submission.get('server_name', ''),
            "u_requested_by": submission.get('submitted_by', ''),
            "ticket_id": submission.get('submission_id', '')
        }
        r = requests.post(
            f"{SNOW_CONNECTOR_URL}/api/create_ticket",
            json=payload,
            timeout=30
        )
        if r.status_code == 200:
            return {"success": True, "data": r.json()}
        elif r.status_code >= 500 and attempt < MAX_RETRIES:
            backoff = BACKOFF_BASE * (2 ** (attempt - 1))
            log.warning(f"SNOW connector returned {r.status_code}. Retrying in {backoff}s (attempt {attempt})")
            time.sleep(backoff)
            return call_snow_connector_create_ticket(submission, attempt + 1)
        else:
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text}"}
    except requests.exceptions.Timeout:
        if attempt < MAX_RETRIES:
            backoff = BACKOFF_BASE * (2 ** (attempt - 1))
            log.warning(f"SNOW connector timeout. Retrying in {backoff}s (attempt {attempt})")
            time.sleep(backoff)
            return call_snow_connector_create_ticket(submission, attempt + 1)
        return {"success": False, "error": "Timeout after max retries"}
    except Exception as e:
        log.error(f"SNOW connector error: {e}")
        return {"success": False, "error": str(e)}


def update_submission_snow_ticket(submission_id: str, ticket_id: str, sys_id: str = "") -> bool:
    sys_id_val = f", snow_ticket_sys_id = '{sys_id}'" if sys_id else ""
    sql = f"""
    UPDATE mcp_submissions 
    SET snow_ticket_id = '{ticket_id}'{sys_id_val}
    WHERE submission_id = '{submission_id}'
    """
    return ws_execute(sql)


def write_audit_event(submission_id: str, event_type: str, detail: str):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        "id": f"snow_wire_{submission_id}_{int(time.time())}",
        "target_server_id": submission_id,
        "event_type": event_type,
        "actor": "snow_connector_integration_wiring",
        "detail": detail,
        "created_at": ts
    }
    ws_write("audit_log", row)


def process_pending_submissions():
    pending = get_pending_snow_submissions()
    if not pending:
        log.debug("No pending SNOW submissions found.")
        return 0
    
    log.info(f"Found {len(pending)} pending submissions for SNOW ticket creation.")
    processed = 0
    
    for sub in pending:
        submission_id = sub.get('submission_id', '')
        server_name = sub.get('server_name', 'Unknown')
        
        log.info(f"Processing submission {submission_id}: {server_name}")
        
        result = call_snow_connector_create_ticket(sub)
        
        if result.get("success"):
            data = result.get("data", {})
            ticket_id = data.get("ticket_id", data.get("sys_id", ""))
            sys_id = data.get("sys_id", "")
            
            if update_submission_snow_ticket(submission_id, ticket_id, sys_id):
                write_audit_event(
                    submission_id,
                    "SNOW_TICKET_CREATED",
                    f"Created SNOW ticket {ticket_id} for {server_name}"
                )
                log.info(f"Updated submission {submission_id} with ticket {ticket_id}")
                processed += 1
            else:
                log.error(f"Failed to update submission {submission_id}")
                write_audit_event(
                    submission_id,
                    "SNOW_TICKET_UPDATE_FAILED",
                    f"Failed to update ticket {ticket_id} for {server_name}"
                )
        else:
            error = result.get("error", "Unknown error")
            log.error(f"SNOW connector failed for {submission_id}: {error}")
            write_audit_event(
                submission_id,
                "SNOW_TICKET_CREATE_FAILED",
                f"SNOW connector failed: {error}"
            )
    
    return processed


def cycle():
    log.info("Running integration cycle...")
    processed = process_pending_submissions()
    send_heartbeat("running", f"processed={processed}")
    return processed


def run():
    log.info(f"Starting {SERVICE_NAME}...")
    
    if not check_single_instance():
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    send_heartbeat("starting", "")
    
    log.info(f"{SERVICE_NAME} running. Polling every {POLL_SECS}s, heartbeat every {HEARTBEAT_INTERVAL}s.")
    
    last_heartbeat = time.time()
    
    while True:
        try:
            cycle()
            
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat("running", "heartbeat")
                last_heartbeat = now
            
        except SystemExit:
            break
        except Exception as e:
            log.error(f"Error in cycle: {e}")
            send_heartbeat("error", str(e))
        
        time.sleep(POLL_SECS)
    
    remove_pid_file()
    log.info(f"{SERVICE_NAME} stopped.")


if __name__ == '__main__':
    run()