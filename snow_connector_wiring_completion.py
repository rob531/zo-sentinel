import logging
import os
import signal
import sys
import time
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "snow_connector_wiring_completion"
SERVICE_PORT = 0
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
PID_FILE = "/tmp/snow_connector_wiring_completion.pid"
LOG_FILE = "/home/workspace/logs/snow_connector_wiring_completion.log"
HEARTBEAT_INTERVAL = 60
SNOW_WEBHOOK_TIMEOUT = 10

LOG = logging.getLogger(__name__)

def setup_logging():
    log_dir = Path(LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout)
        ]
    )

def ws_query(sql):
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=SNOW_WEBHOOK_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        LOG.error(f"ws_query failed: {e}")
        return []

def ws_write(table, rows):
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=SNOW_WEBHOOK_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        LOG.error(f"ws_write failed for {table}: {e}")
        return {"ok": False}

def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=SNOW_WEBHOOK_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        LOG.error(f"ws_execute failed: {e}")
        return {"ok": False}

def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            LOG.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ProcessLookupError, ValueError):
            LOG.warning(f"Stale PID file found: {old_pid}")
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    LOG.info(f"PID file created: {PID_FILE}")

def remove_pid_file():
    try:
        Path(PID_FILE).unlink()
        LOG.info("PID file removed")
    except Exception as e:
        LOG.warning(f"Failed to remove PID file: {e}")

def signal_handler(signum, frame):
    LOG.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def send_heartbeat():
    ts = datetime.now(timezone.utc).isoformat()
    rows = [{
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "running",
        "meta": "{}"
    }]
    ws_write("service_health", rows)
    LOG.debug(f"Heartbeat sent at {ts}")

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_snow_oauth_token():
    client_id = os.environ.get("SNOW_CLIENT_ID")
    client_secret = os.environ.get("SNOW_CLIENT_SECRET")
    if not client_id or not client_secret:
        LOG.error("SNOW_CLIENT_ID or SNOW_CLIENT_SECRET not set in environment")
        return None
    try:
        token_url = os.environ.get("SNOW_TOKEN_URL", "https://dev12345.service-now.com/oauth_token.do")
        resp = requests.post(token_url, data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }, timeout=SNOW_WEBHOOK_TIMEOUT)
        resp.raise_for_status()
        token_data = resp.json()
        return token_data.get("access_token")
    except Exception as e:
        LOG.error(f"Failed to obtain SNOW OAuth token: {e}")
        return None

def validate_snow_webhook_signature(payload_body, signature, secret_key=None):
    if not signature:
        LOG.warning("No webhook signature provided")
        return False
    secret = secret_key or os.environ.get("SNOW_WEBHOOK_SECRET", "")
    if not secret:
        LOG.warning("No SNOW_WEBHOOK_SECRET configured")
        return False
    expected_sig = hmac.new(secret.encode(), payload_body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_sig)

def get_pending_snow_submissions():
    sql = """
    SELECT 
        submission_id,
        server_id,
        mcp_name,
        submitter_email,
        description,
        source,
        status,
        created_at
    FROM mcp_submissions 
    WHERE source LIKE '%snow%' 
       OR source LIKE '%servicenow%'
       OR source = 'snow_webhook'
    """
    return ws_query(sql)

def get_decision_columns():
    sql = "SELECT column_name FROM information_schema.columns WHERE table_name = 'mcp_decisions'"
    cols = ws_query(sql)
    return [c["column_name"] for c in cols] if cols else []

def write_verdict_decision(submission_id, server_id, verdict, risk_tier, decision_detail, analyst_email=None):
    columns = get_decision_columns()
    if not columns:
        LOG.warning("mcp_decisions table columns not found, using default schema")
        columns = ["submission_id", "server_id", "verdict", "risk_tier", "decision_detail", "decided_at"]
    ts = utc_now_iso()
    row = {
        "submission_id": submission_id,
        "server_id": server_id,
        "verdict": verdict,
        "risk_tier": risk_tier,
        "decision_detail": decision_detail,
        "decided_at": ts
    }
    if "analyst_email" in columns and analyst_email:
        row["analyst_email"] = analyst_email
    if "created_at" in columns:
        row["created_at"] = ts
    ws_write("mcp_decisions", [row])
    LOG.info(f"Wrote decision for submission {submission_id}: verdict={verdict}, risk_tier={risk_tier}")

def update_submission_status(submission_id, new_status):
    sql = f"UPDATE mcp_submissions SET status = '{new_status}' WHERE submission_id = '{submission_id}'"
    return ws_execute(sql)

def get_snow_approval_record(snow_sys_id, snow_table=None):
    snow_table = snow_table or os.environ.get("SNOW_APPROVAL_TABLE", "sysapproval_approver")
    sql = f"""
    SELECT 
        sys_id,
        state,
        approval,
        comments,
        started_at,
        approver
    FROM {snow_table}
    WHERE sys_id = '{snow_sys_id}'
    LIMIT 1
    """
    return ws_query(sql)

def get_snow_incident_details(snow_sys_id):
    incident_table = os.environ.get("SNOW_INCIDENT_TABLE", "incident")
    sql = f"""
    SELECT 
        sys_id,
        number,
        state,
        short_description,
        description,
        caller_id,
        assigned_to,
        u_mcp_server_id
    FROM {incident_table}
    WHERE sys_id = '{snow_sys_id}'
    LIMIT 1
    """
    return ws_query(sql)

def map_snow_state_to_verdict(snow_state, snow_approval=None):
    state_map = {
        "approved": ("TRUSTED", "ENTERPRISE_CONTROLLED"),
        "rejected": ("UNTRUSTED", "HIGH_RISK_ISOLATED"),
        "request": ("AMBER_UNVERIFIED", "CAUTION_LIMITED"),
        "pending": ("AMBER_UNVERIFIED", "CAUTION_LIMITED"),
    }
    if snow_approval and snow_approval.lower() in ["yes", "true", "approve"]:
        return state_map["approved"]
    snow_state_lower = str(snow_state).lower() if snow_state else ""
    if "approve" in snow_state_lower:
        return state_map["approved"]
    if "reject" in snow_state_lower:
        return state_map["rejected"]
    if "pend" in snow_state_lower or "request" in snow_state_lower:
        return state_map["pending"]
    return state_map["pending"]

def process_snow_webhook_event(event_payload):
    snow_sys_id = event_payload.get("sys_id") or event_payload.get("u_mcp_server_id")
    snow_table = event_payload.get("table", "incident")
    if not snow_sys_id:
        LOG.warning("No SNOW sys_id in webhook event")
        return False
    snow_state = event_payload.get("state", "pending")
    snow_approval = event_payload.get("approval")
    verdict, risk_tier = map_snow_state_to_verdict(snow_state, snow_approval)
    sql = f"""
    SELECT submission_id, server_id 
    FROM mcp_submissions 
    WHERE description LIKE '%{snow_sys_id}%' 
       OR description LIKE '%SNOW:{snow_sys_id}%'
    LIMIT 5
    """
    matches = ws_query(sql)
    if not matches:
        LOG.info(f"No submission match found for SNOW sys_id: {snow_sys_id}")
        return False
    processed = False
    for match in matches:
        submission_id = match.get("submission_id")
        server_id = match.get("server_id")
        decision_detail = json.dumps({
            "source": "snow_webhook",
            "snow_sys_id": snow_sys_id,
            "snow_table": snow_table,
            "snow_state": snow_state,
            "processed_at": utc_now_iso()
        })
        analyst_email = event_payload.get("caller_id") or event_payload.get("assigned_to")
        write_verdict_decision(submission_id, server_id, verdict, risk_tier, decision_detail, analyst_email)
        update_submission_status(submission_id, "decided")
        processed = True
    if processed:
        LOG.info(f"Processed SNOW webhook for sys_id: {snow_sys_id}")
    return processed

def verify_tables_exist():
    required_tables = ["mcp_submissions", "mcp_decisions", "service_health"]
    for tbl in required_tables:
        sql = f"SELECT 1 FROM information_schema.tables WHERE table_name = '{tbl}'"
        result = ws_query(sql)
        if not result:
            LOG.error(f"Required table '{tbl}' not found in database")
            return False
    LOG.info("All required tables verified")
    return True

def load_processed_events():
    processed_file = Path("/tmp/snow_wiring_processed_events.txt")
    if processed_file.exists():
        return set(processed_file.read_text().strip().split("\n"))
    return set()

def save_processed_events(events):
    processed_file = Path("/tmp/snow_wiring_processed_events.txt")
    processed_file.write_text("\n".join(events))
    LOG.info(f"Saved {len(events)} processed event IDs")

def cycle():
    LOG.debug("Starting snow connector wiring cycle")
    verify_tables_exist()
    snow_token = get_snow_oauth_token()
    if not snow_token:
        LOG.warning("Could not obtain SNOW OAuth token, will retry next cycle")
        return
    pending = get_pending_snow_submissions()
    if not pending:
        LOG.debug("No pending SNOW submissions found")
        return
    processed_events = load_processed_events()
    new_processed = list(processed_events)
    for submission in pending:
        submission_id = submission.get("submission_id", "")
        if submission_id in processed_events:
            continue
        description = submission.get("description", "")
        snow_match = None
        for keyword in ["SNOW:", "snow:", "sys_id=", "SNOW_ID"]:
            if keyword in description:
                parts = description.split(keyword)
                if len(parts) > 1:
                    snow_match = parts[1].split()[split_idx] if (split_idx := 0) else parts[1].split()[0]
                    break
        if snow_match:
            LOG.info(f"Found SNOW reference in submission {submission_id}: {snow_match}")
            dummy_event = {
                "sys_id": snow_match,
                "state": "pending",
                "source": "wiring_cycle"
            }
            process_snow_webhook_event(dummy_event)
            new_processed.append(submission_id)
    if len(new_processed) > 1000:
        new_processed = new_processed[-1000:]
    save_processed_events(new_processed)
    LOG.debug("Completed snow connector wiring cycle")

def heartbeat_loop():
    last_heartbeat = time.time()
    while True:
        try:
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = now
            time.sleep(5)
        except Exception as e:
            LOG.error(f"Heartbeat loop error: {e}")
            time.sleep(10)

def run():
    LOG.info(f"Starting {SERVICE_NAME}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    send_heartbeat()
    LOG.info("Snow connector wiring completion daemon started")
    import threading
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    poll_interval = int(os.environ.get("SNOW_WIRING_POLL_SECS", "30"))
    while True:
        try:
            cycle()
            time.sleep(poll_interval)
        except Exception as e:
            LOG.error(f"Run loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    setup_logging()
    run()