#!/usr/bin/env python3
"""
snow_integration_wiring.py  -- ZO-SENTINEL
Wires snow_connector.py into approval_workflow.py via write_service polling.
Polls mcp_submissions for pending ServiceNow requests and initiates approval workflow.

Port: Uses existing services (snow_connector on 8778, approval_workflow on 8780)
All inter-daemon communication via write_service only.
"""
import sys
import time
import logging
import os
import signal
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = "snow_integration_wiring"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = "/tmp/snow_integration_wiring.log"
POLL_SECS = 30
HEARTBEAT_INTERVAL = 60
EXTERNAL_TIMEOUT = 10
WRITE_SERVICE_TIMEOUT = 30

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [REDACTED]',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Already running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            log.warning(f"Stale PID file, removing {old_pid}")
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Remove PID file on exit."""
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service with timeout."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=WRITE_SERVICE_TIMEOUT
        )
        if response.status_code == 200:
            return True
        log.error(f"ws_write failed: {response.status_code} - {response.text}")
        return False
    except requests.exceptions.Timeout:
        log.error(f"ws_write timeout for table {table}")
        return False
    except Exception as e:
        log.error(f"ws_write error for table {table}: {e}")
        return False


def ws_query(sql: str, limit: int = 1000) -> List[Dict[str, Any]]:
    """Query write_service with timeout."""
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql, "limit": limit},
            timeout=EXTERNAL_TIMEOUT
        )
        if response.status_code == 200:
            result = response.json()
            return result.get("rows", [])
        log.error(f"ws_query failed: {response.status_code} - {response.text}")
        return []
    except requests.exceptions.Timeout:
        log.error(f"ws_query timeout for SQL: {sql[:100]}")
        return []
    except Exception as e:
        log.error(f"ws_query error: {e}")
        return []


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/execute",
            json={"sql": sql},
            timeout=WRITE_SERVICE_TIMEOUT
        )
        if response.status_code == 200:
            return True
        log.error(f"ws_execute failed: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        log.error(f"ws_execute error: {e}")
        return False


def send_heartbeat():
    """Send heartbeat to service_health."""
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": now
    })
    log.debug(f"Heartbeat sent at {now}")


def get_pending_snow_submissions() -> List[Dict[str, Any]]:
    """Fetch pending ServiceNow submissions from mcp_submissions table."""
    sql = """
    SELECT id, mcp_name, requester, short_description, description, 
           source, created_at, metadata
    FROM mcp_submissions 
    WHERE source = 'snow' AND verdict IS NULL
    ORDER BY created_at ASC
    LIMIT 100
    """
    return ws_query(sql)


def initiate_approval_workflow(submission: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Call approval_workflow API to initiate assessment for a snow submission.
    Returns the workflow response or None on failure.
    """
    try:
        payload = {
            "mcp_name": submission.get("mcp_name", ""),
            "requester": submission.get("requester", ""),
            "short_description": submission.get("short_description", ""),
            "description": submission.get("description", ""),
            "source": "snow",
            "external_id": str(submission.get("id", "")),
            "metadata": submission.get("metadata", {})
        }
        response = requests.post(
            f"{APPROVAL_WORKFLOW_URL}/api/submit",
            json=payload,
            timeout=EXTERNAL_TIMEOUT
        )
        if response.status_code in (200, 201):
            log.info(f"Initiated approval workflow for submission {submission.get('id')}")
            return response.json()
        else:
            log.error(f"Approval workflow error: {response.status_code} - {response.text}")
            return None
    except requests.exceptions.Timeout:
        log.error(f"Approval workflow timeout for submission {submission.get('id')}")
        return None
    except Exception as e:
        log.error(f"Approval workflow error for submission {submission.get('id')}: {e}")
        return None


def update_submission_status(submission_id: int, status: str, workflow_ref: str = None) -> bool:
    """Update mcp_submissions with workflow initiation status."""
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": submission_id,
        "workflow_status": status,
        "workflow_ref": workflow_ref,
        "workflow_initiated_at": now
    }
    return ws_write("mcp_submissions", row)


def ensure_tables():
    """Ensure required tables exist with proper schema."""
    create_submissions_table = """
    CREATE TABLE IF NOT EXISTS mcp_submissions (
        id INTEGER PRIMARY KEY,
        mcp_name VARCHAR,
        requester VARCHAR,
        short_description VARCHAR,
        description VARCHAR,
        source VARCHAR,
        verdict VARCHAR,
        workflow_status VARCHAR,
        workflow_ref VARCHAR,
        workflow_initiated_at VARCHAR,
        created_at VARCHAR,
        updated_at VARCHAR,
        metadata JSON
    )
    """
    ws_execute(create_submissions_table)
    log.info("Ensured mcp_submissions table exists")


def process_pending_submissions():
    """Main processing loop for pending snow submissions."""
    submissions = get_pending_snow_submissions()
    if not submissions:
        log.debug("No pending snow submissions found")
        return 0

    log.info(f"Found {len(submissions)} pending snow submissions")
    processed = 0

    for submission in submissions:
        submission_id = submission.get("id")
        mcp_name = submission.get("mcp_name", "")

        if not submission_id:
            continue

        workflow_result = initiate_approval_workflow(submission)

        if workflow_result:
            workflow_ref = workflow_result.get("submission_id") or workflow_result.get("id")
            update_submission_status(submission_id, "assessment_initiated", str(workflow_ref))
            processed += 1
            log.info(f"Processed submission {submission_id}: {mcp_name}")
        else:
            update_submission_status(submission_id, "workflow_error", None)
            log.warning(f"Failed to process submission {submission_id}: {mcp_name}")

    return processed


def check_services_health() -> bool:
    """Check that required services are healthy."""
    try:
        response = requests.get(f"{APPROVAL_WORKFLOW_URL}/health", timeout=5)
        if response.status_code == 200:
            return True
        log.warning(f"Approval workflow health check failed: {response.status_code}")
        return False
    except Exception as e:
        log.warning(f"Approval workflow unreachable: {e}")
        return False


def cycle():
    """Single processing cycle."""
    log.debug("Starting processing cycle")

    if not check_services_health():
        log.warning("Approval workflow service not healthy, skipping cycle")

    try:
        processed = process_pending_submissions()
        log.info(f"Cycle complete: processed {processed} submissions")
    except Exception as e:
        log.error(f"Cycle error: {e}")


def run():
    """Main daemon run loop."""
    log.info(f"Starting {SERVICE_NAME}")

    if not check_single_instance():
        sys.exit(1)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    ensure_tables()
    send_heartbeat()

    log.info(f"{SERVICE_NAME} running, polling every {POLL_SECS}s")
    log.info(f"Approval workflow URL: {APPROVAL_WORKFLOW_URL}")

    try:
        while True:
            cycle()
            send_heartbeat()
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()