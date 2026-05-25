#!/usr/bin/env python3
"""
snow_connector_wiring.py  -- ZO-SENTINEL
Wires snow_connector (port 8778) to approval_workflow (port 8780).

Flow:
  1. snow_connector receives ServiceNow webhook
  2. snow_connector writes to mcp_submissions (source='servicenow', status='pending_review')
  3. This wiring polls for pending servicenow submissions
  4. Transforms and forwards to approval_workflow /api/submit
  5. Updates mcp_submissions status

All DB access via write_service (127.0.0.1:8772) — never direct DuckDB.
"""
import os
import sys
import time
import signal
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────────────────────
SERVICE_NAME = "snow_connector_wiring"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"
SNOW_CONNECTOR_URL = "http://127.0.0.1:8778"
POLL_SECONDS = 30

# ─── Logging ─────────────────────────────────────────────────────────────────
log = logging.getLogger(SERVICE_NAME)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)


# ─── Write Service Helpers ───────────────────────────────────────────────────

def ws_query(sql: str, limit: int = 100) -> list:
    """Query write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql, "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    """Write to write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        log.error(f"ws_write {table} failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DML via write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/execute",
            json={"sql": sql, "wait": True},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


# ─── Health Checks ──────────────────────────────────────────────────────────

def check_service_alive(url: str, name: str) -> bool:
    """Check if a service is responding."""
    try:
        resp = requests.get(f"{url}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        log.warning(f"{name} not reachable at {url}")
        return False


def check_snow_connector_alive() -> bool:
    return check_service_alive(SNOW_CONNECTOR_URL, "snow_connector")


def check_approval_workflow_alive() -> bool:
    return check_service_alive(APPROVAL_WORKFLOW_URL, "approval_workflow")


# ─── Heartbeat ───────────────────────────────────────────────────────────────

def send_heartbeat():
    """Send heartbeat to service_health."""
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now(timezone.utc).isoformat() + "Z",
        "status": "ok",
    }])


# ─── Core Logic ──────────────────────────────────────────────────────────────

def fetch_pending_snow_submissions() -> list:
    """Fetch ServiceNow submissions awaiting approval workflow forwarding."""
    rows = ws_query(
        """SELECT id, snow_ticket_id, mcp_server_name, requester_email,
                  short_description, submitted_at, status
           FROM mcp_submissions
           WHERE source = 'servicenow'
             AND (status = 'pending_review' OR status = 'pending')
           ORDER BY submitted_at ASC
           LIMIT 50""",
        limit=50,
    )
    return rows


def transform_to_approval_payload(row: dict) -> dict:
    """
    Transform a snow_connector mcp_submissions record
    into an approval_workflow /api/submit payload.
    """
    # Determine requester team from email domain if available
    requestor_email = row.get("requester_email", "")
    if requestor_email and "@" in requestor_email:
        team = requestor_email.split("@")[1].split(".")[0].title()
    else:
        team = "ServiceNow"

    return {
        "mcp_identifier": row.get("mcp_server_name") or row.get("short_description") or "unknown",
        "requester_name": requestor_email or "unknown",
        "requester_team": team,
        "business_purpose": row.get("short_description", "")[:500],
        "environment": "Production",
    }


def update_submission_status(submission_id: str, snow_ticket_id: str, new_status: str, detail: str = ""):
    """Update mcp_submissions status and optional detail fields."""
    now = datetime.now(timezone.utc).isoformat() + "Z"
    if snow_ticket_id:
        sql = f"UPDATE mcp_submissions SET status = '{new_status}', processed_at = '{now}' WHERE snow_ticket_id = '{snow_ticket_id}'"
        ws_execute(sql)
    elif submission_id:
        sql = f"UPDATE mcp_submissions SET status = '{new_status}', processed_at = '{now}' WHERE submission_id = '{submission_id}'"
        ws_execute(sql)

    # Write audit trail
    ws_write("audit_log", [{
        "event_type": f"snow_wiring_status_{new_status}",
        "actor": SERVICE_NAME,
        "detail": detail or f"Status updated to {new_status}",
        "created_at": now,
    }])


def forward_to_approval_workflow(row: dict) -> bool:
    """Forward a snow_connector submission to approval_workflow."""
    snow_ticket_id = row.get("snow_ticket_id", "") or ""
    submission_id = row.get("id", "") or ""

    try:
        payload = transform_to_approval_payload(row)
        resp = requests.post(
            f"{APPROVAL_WORKFLOW_URL}/api/submit",
            json=payload,
            timeout=20,
        )

        if resp.status_code == 200:
            result = resp.json()
            approval_id = result.get("submission_id", "")
            log.info(
                f"Forwarded {snow_ticket_id} → approval_workflow "
                f"(id={approval_id})"
            )
            update_submission_status(
                submission_id, snow_ticket_id, "forwarded_to_approval",
                f"Forwarded to approval_workflow. approval_id={approval_id}"
            )
            return True
        else:
            log.error(
                f"approval_workflow rejected {snow_ticket_id}: "
                f"status={resp.status_code} body={resp.text[:200]}"
            )
            update_submission_status(
                submission_id, snow_ticket_id, "approval_rejected",
                f"approval_workflow returned {resp.status_code}: {resp.text[:200]}"
            )
            return False

    except requests.RequestException as e:
        log.error(f"Failed to forward {snow_ticket_id}: {e}")
        update_submission_status(
            submission_id, snow_ticket_id, "forward_error",
            f"Forward error: {str(e)[:200]}"
        )
        return False


# ─── Main Cycle ──────────────────────────────────────────────────────────────

def cycle() -> int:
    """Process one polling cycle. Returns count of forwarded tickets."""
    if not check_snow_connector_alive():
        log.warning("snow_connector not alive — skipping cycle")
        return 0

    if not check_approval_workflow_alive():
        log.warning("approval_workflow not alive — skipping cycle")
        return 0

    rows = fetch_pending_snow_submissions()
    if not rows:
        log.debug("No pending ServiceNow submissions")
        return 0

    log.info(f"Found {len(rows)} pending ServiceNow submissions")
    forwarded = 0

    for row in rows:
        try:
            if forward_to_approval_workflow(row):
                forwarded += 1
        except Exception as e:
            log.exception(f"Error processing row: {e}")

    log.info(f"Cycle complete: {forwarded}/{len(rows)} forwarded")
    return forwarded


# ─── Instance Guard ──────────────────────────────────────────────────────────

def check_single_instance():
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE).read().strip())
            os.kill(pid, 0)
            log.error(f"Another instance running as PID {pid} — exit")
            sys.exit(1)
        except OSError:
            pass  # Stale PID file
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    log.info(f"Signal {signum} — shutting down")
    remove_pid_file()
    sys.exit(0)


# ─── Entry Point ─────────────────────────────────────────────────────────────

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"{SERVICE_NAME} starting")

    while True:
        try:
            n = cycle()
        except Exception as e:
            log.exception(f"Cycle error: {e}")
            n = 0

        send_heartbeat()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()