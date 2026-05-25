import os
import sys
import time
import json
import logging
import signal
import hashlib
from datetime import datetime, timezone
from typing import Optional

import requests

# --- Constants ---
SERVICE_NAME = "snow_connector_integration_completion"
SERVICE_PORT = 0  # Not an HTTP service, runs as daemon
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
POLL_SECS = 60
BATCH_SIZE = 50

# ServiceNow credentials from environment
SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")
SNOW_USERNAME = os.environ.get("SNOW_USERNAME", "")
SNOW_PASSWORD = os.environ.get("SNOW_PASSWORD", "")

# Token cache
_cached_token: Optional[str] = None
_token_expires_at: float = 0

# --- Logger ---
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# --- Write Service Helpers ---
def ws_query(sql: str) -> list:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return False


# --- ServiceNow OAuth2 Token ---
def get_snow_oauth_token() -> Optional[str]:
    global _cached_token, _token_expires_at
    now = time.time()
    if _cached_token and now < _token_expires_at - 60:
        return _cached_token

    if not SNOW_CLIENT_ID or not SNOW_CLIENT_SECRET or not SNOW_INSTANCE:
        logger.warning("ServiceNow OAuth credentials not configured")
        return None

    token_url = f"https://{SNOW_INSTANCE}.service-now.com/oauth_token.do"
    data = {
        "grant_type": "client_credentials",
        "client_id": SNOW_CLIENT_ID,
        "client_secret": SNOW_CLIENT_SECRET,
    }
    try:
        resp = requests.post(token_url, data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        _cached_token = result.get("access_token")
        expires_in = result.get("expires_in", 3600)
        _token_expires_at = now + expires_in
        logger.info("ServiceNow OAuth token refreshed")
        return _cached_token
    except Exception as e:
        logger.error(f"Failed to get ServiceNow OAuth token: {e}")
        return None


def get_snow_basic_auth() -> Optional[tuple]:
    if SNOW_USERNAME and SNOW_PASSWORD:
        return (SNOW_USERNAME, SNOW_PASSWORD)
    return None


# --- ServiceNow API Call ---
def fetch_snow_ticket(ticket_number: str) -> Optional[dict]:
    token = get_snow_oauth_token()
    auth = get_snow_basic_auth()

    if not token and not auth:
        logger.warning("No ServiceNow authentication available")
        return None

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/incident"
    params = {"number": ticket_number, "sysparm_limit": 1}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 404:
            logger.debug(f"Ticket {ticket_number} not found in ServiceNow")
            return None
        resp.raise_for_status()
        result = resp.json()
        records = result.get("result", [])
        if records:
            return records[0]
        return None
    except Exception as e:
        logger.error(f"Failed to fetch ServiceNow ticket {ticket_number}: {e}")
        return None


# --- Correlation Logic ---
def correlate_ticket_priority(ticket: dict, registry_entry: Optional[dict]) -> dict:
    priority_map = {
        "1": "critical",
        "2": "high",
        "3": "medium",
        "4": "low",
        "5": "low",
    }
    ticket_priority = ticket.get("priority", "3")
    ticket_severity = ticket.get("severity", "")
    mapped_priority = priority_map.get(str(ticket_priority), "medium")

    correlation = {
        "ticket_priority": ticket_priority,
        "registry_priority": registry_entry.get("risk_tier", "unknown") if registry_entry else "unknown",
        "matched_priority": mapped_priority,
        "state": ticket.get("state", ""),
        "assigned_to": ticket.get("assigned_to", {}).get("display_value", "") if isinstance(ticket.get("assigned_to"), dict) else ticket.get("assigned_to", ""),
        "short_description": ticket.get("short_description", ""),
        "correlation_id": ticket.get("sys_id", ""),
    }

    if registry_entry:
        risk_tier = registry_entry.get("risk_tier", "unknown")
        if mapped_priority in ["critical", "high"] and risk_tier in ["HIGH_RISK_ISOLATED", "KNOWN_THREAT"]:
            correlation["alert_level"] = "elevated"
        elif mapped_priority == "low" and risk_tier in ["TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"]:
            correlation["alert_level"] = "normal"
        else:
            correlation["alert_level"] = "neutral"
    else:
        correlation["alert_level"] = "no_registry_match"

    return correlation


def extract_ticket_number_from_submission(submission: dict) -> Optional[str]:
    snow_ticket_id = submission.get("snow_ticket_id", "")
    if snow_ticket_id and snow_ticket_id.strip():
        return snow_ticket_id.strip()
    return None


# --- Main Integration Logic ---
def get_pending_submissions() -> list:
    sql = f"""
    SELECT 
        ms.submission_id,
        ms.server_id,
        ms.name,
        ms.url,
        ms.status,
        ms.submitted_at,
        ms.snow_ticket_id,
        r.verdict,
        r.risk_tier,
        r.trust_score
    FROM mcp_submissions ms
    LEFT JOIN mcp_server_registry r ON ms.server_id = r.server_id
    WHERE ms.status = 'pending'
    ORDER BY ms.submitted_at DESC
    LIMIT {BATCH_SIZE}
    """
    return ws_query(sql)


def update_submission_snow_ticket(submission_id: str, correlation_data: dict, snow_ticket_id: str) -> bool:
    correlation_json = json.dumps(correlation_data).replace("'", "''")
    sql = f"""
    UPDATE mcp_submissions
    SET snow_ticket_id = '{snow_ticket_id}',
        correlation_data = '{correlation_json}',
        updated_at = '{utc_now_iso()}'
    WHERE submission_id = '{submission_id}'
    """
    return ws_execute(sql)


def mark_submission_correlated(submission_id: str) -> bool:
    sql = f"""
    UPDATE mcp_submissions
    SET status = 'correlated',
        updated_at = '{utc_now_iso()}'
    WHERE submission_id = '{submission_id}'
    """
    return ws_execute(sql)


def process_pending_correlations() -> int:
    submissions = get_pending_submissions()
    if not submissions:
        return 0

    processed = 0
    for submission in submissions:
        submission_id = submission.get("submission_id")
        server_id = submission.get("server_id")
        snow_ticket_id = extract_ticket_number_from_submission(submission)

        if not snow_ticket_id:
            logger.debug(f"Submission {submission_id} has no snow_ticket_id, skipping")
            continue

        ticket = fetch_snow_ticket(snow_ticket_id)
        if not ticket:
            logger.warning(f"Could not fetch ticket {snow_ticket_id} for submission {submission_id}")
            continue

        registry_entry = None
        if server_id:
            sql = f"SELECT verdict, risk_tier, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'"
            rows = ws_query(sql)
            if rows:
                registry_entry = rows[0]

        correlation = correlate_ticket_priority(ticket, registry_entry)

        if update_submission_snow_ticket(submission_id, correlation, snow_ticket_id):
            mark_submission_correlated(submission_id)
            logger.info(f"Correlated submission {submission_id} with ticket {snow_ticket_id}: {correlation.get('alert_level')}")
            processed += 1
        else:
            logger.error(f"Failed to update submission {submission_id}")

    return processed


def cycle() -> int:
    try:
        return process_pending_correlations()
    except Exception as e:
        logger.error(f"cycle error: {e}")
        return 0


def send_heartbeat() -> None:
    try:
        rows = [{"service": SERVICE_NAME, "status": "running", "ts": utc_now_iso(), "meta": json.dumps({})}]
        ws_write("service_health", rows)
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")


# --- Single Instance Guard ---
def check_single_instance() -> bool:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        if old_pid != os.getpid():
            try:
                os.kill(old_pid, 0)
                logger.warning(f"Already running as PID {old_pid}")
                return False
            except OSError:
                pass
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame) -> None:
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


# --- Daemon Run Loop ---
def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        logger.warning(f"{SERVICE_NAME} already running, exiting")
        sys.exit(1)

    logger.info(f"{SERVICE_NAME} starting")

    while True:
        try:
            processed = cycle()
            send_heartbeat()
            if processed > 0:
                logger.info(f"Cycle complete: processed {processed} correlations")
            else:
                logger.debug("Cycle complete: no pending correlations")
        except Exception as e:
            logger.error(f"Run loop error: {e}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(LOG_FILE)],
    )
    run()