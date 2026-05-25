import logging
import os
import sys
import time
import json
import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "snow_connector_approval_workflow_wiring"
SERVICE_PORT = 0
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)

SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")
SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")
SNOW_TABLE = "u_mcp_server_request"
POLL_SECS = 30


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> None:
    pid = str(os.getpid())
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        if old_pid and old_pid != pid:
            try:
                os.kill(int(old_pid), 0)
                log.error("Another instance is running with PID %s. Exiting.", old_pid)
                sys.exit(1)
            except (OSError, ValueError):
                log.warning("Stale PID file found, removing.")
                pid_file.unlink()
    pid_file.write_text(pid)
    log.info("Acquired PID file: %s", PID_FILE)


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.warning("Failed to remove PID file: %s", e)


def signal_handler(signum, frame) -> None:
    log.info("Received signal %d, shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def get_db_path() -> str:
    return "/home/workspace/Datasets/zo-sentinel/sentinel.db"


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed: %s | table: %s", e, table)
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def get_snow_oauth_token() -> str | None:
    if not SNOW_CLIENT_ID or not SNOW_CLIENT_SECRET or not SNOW_INSTANCE:
        log.error("SNOW credentials not configured in environment.")
        return None
    token_url = f"https://{SNOW_INSTANCE}.service-now.com/oauth_token.do"
    data = {
        "grant_type": "client_credentials",
        "client_id": SNOW_CLIENT_ID,
        "client_secret": SNOW_CLIENT_SECRET
    }
    try:
        resp = requests.post(token_url, data=data, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get("access_token")
    except Exception as e:
        log.error("Failed to obtain SNOW OAuth token: %s", e)
        return None


def verify_snow_webhook_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not secret:
        log.warning("No SNOW webhook secret configured, skipping signature verification.")
        return True
    if not signature:
        log.error("No webhook signature provided - rejecting unsigned webhook.")
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(f"sha256={expected}", signature):
        log.error("Webhook signature mismatch - rejecting forged request.")
        return False
    return True


def sanitize_for_log(value: str) -> str:
    if not value:
        return ""
    redacted_fields = {"password", "secret", "token", "authorization", "pwd_robinhood"}
    sanitized = value
    for field in redacted_fields:
        if field.lower() in value.lower():
            sanitized = "[REDACTED]"
            break
    if len(sanitized) > 200:
        sanitized = sanitized[:200] + "..."
    return sanitized


def check_prompt_injection(text: str) -> bool:
    if not text:
        return False
    injection_patterns = [
        "--", "; DROP", "'; DROP", '"; DROP', "UNION SELECT",
        "exec(", "eval(", "<script", "javascript:", "${", "#{"
    ]
    text_lower = text.lower()
    for pattern in injection_patterns:
        if pattern.lower() in text_lower:
            log.warning("Potential prompt injection detected: %s", pattern)
            return True
    return False


def get_server_verdict(server_id: str) -> dict | None:
    sql = f"""
    SELECT server_id, name, verdict, trust_score, risk_tier, description
    FROM mcp_server_registry
    WHERE server_id = '{server_id}'
    LIMIT 1
    """
    rows = ws_query(sql)
    return rows[0] if rows else None


def ensure_tables() -> None:
    audit_sql = """
    CREATE TABLE IF NOT EXISTS snow_approval_audit (
        event_id TEXT PRIMARY KEY,
        ticket_number TEXT,
        server_id TEXT,
        event_type TEXT,
        actor TEXT,
        detail TEXT,
        created_at TIMESTAMPTZ,
        snow_response TEXT
    )
    """
    ws_execute(audit_sql)
    log.info("Ensured snow_approval_audit table exists.")


def log_audit_event(event_id: str, ticket_number: str, server_id: str,
                    event_type: str, actor: str, detail: str,
                    snow_response: str | None = None) -> None:
    created_at = utc_now_iso()
    row = {
        "event_id": event_id,
        "ticket_number": ticket_number,
        "server_id": server_id,
        "event_type": event_type,
        "actor": actor,
        "detail": detail,
        "created_at": created_at,
        "snow_response": snow_response or ""
    }
    ws_write("snow_approval_audit", [row])
    log.info("Audit event logged: %s | ticket=%s | type=%s", event_id, ticket_number, event_type)


def compute_ticket_hash(ticket_number: str, updated_at: str) -> str:
    content = f"{ticket_number}:{updated_at}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def is_token_fresh(token: str | None, min_age_seconds: int = 300) -> bool:
    if not token:
        return False
    return True


def get_pending_snow_tickets(oauth_token: str) -> list:
    if not SNOW_INSTANCE:
        log.error("SNOW_INSTANCE not configured.")
        return []
    url = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/{SNOW_TABLE}"
    params = {
        "state": "pending",
        "u_approved": "false",
        "sysparm_limit": "50",
        "sysparm_fields": "number,sys_id,u_server_id,u_request_type,u_requester,u_state,u_updated_on,u_short_description"
    }
    headers = {
        "Authorization": f"Bearer {oauth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get("result", [])
    except Exception as e:
        log.error("Failed to fetch pending SNOW tickets: %s", e)
        return []


def update_snow_ticket(oauth_token: str, ticket_sys_id: str, data: dict) -> bool:
    if not SNOW_INSTANCE:
        log.error("SNOW_INSTANCE not configured.")
        return False
    url = f"https://{SNOW_INSTANCE}.service-now.com/api/now/table/{SNOW_TABLE}/{ticket_sys_id}"
    headers = {
        "Authorization": f"Bearer {oauth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    try:
        resp = requests.patch(url, json=data, headers=headers, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("Failed to update SNOW ticket %s: %s", ticket_sys_id, e)
        return False


def route_to_approval_workflow(server_id: str, ticket_number: str, verdict_data: dict) -> None:
    created_at = utc_now_iso()
    submission_id = hashlib.sha256(f"{server_id}:{created_at}".encode()).hexdigest()[:16]
    row = {
        "submission_id": submission_id,
        "server_id": server_id,
        "verdict": verdict_data.get("verdict", "UNKNOWN"),
        "trust_score": verdict_data.get("trust_score", 0.0),
        "risk_tier": verdict_data.get("risk_tier", "UNKNOWN"),
        "source": "snow_connector",
        "ticket_number": ticket_number,
        "request_type": "approval_review",
        "status": "pending_review",
        "submitted_at": created_at,
        "analyst_email": "",
        "override_reason": "",
        "review_notes": f"SNOW ticket #{ticket_number} requires analyst approval."
    }
    ws_write("approval_workflow", [row])
    log.info("Routed server %s to approval_workflow. submission_id=%s, ticket=%s",
             server_id, submission_id, ticket_number)


def process_snow_ticket(ticket: dict, oauth_token: str) -> bool:
    ticket_number = ticket.get("number", "")
    ticket_sys_id = ticket.get("sys_id", "")
    server_id = ticket.get("u_server_id", "")
    request_type = ticket.get("u_request_type", "")
    updated_at = ticket.get("sys_updated_on", utc_now_iso())
    short_desc = ticket.get("u_short_description", "")
    requester = ticket.get("u_requester", "unknown")
    state = ticket.get("u_state", "pending")

    if not server_id:
        log.warning("SNOW ticket %s has no server_id, skipping.", ticket_number)
        return False

    if check_prompt_injection(server_id) or check_prompt_injection(short_desc):
        log.error("SNOW ticket %s contains potential injection, rejecting.", ticket_number)
        event_id = hashlib.sha256(f"{ticket_number}:reject:{utc_now_iso()}".encode()).hexdigest()[:16]
        log_audit_event(event_id, ticket_number, server_id, "injection_reject",
                        "system", "Prompt injection detected in ticket data")
        return False

    event_id = hashlib.sha256(f"{ticket_number}:{updated_at}".encode()).hexdigest()[:16]
    log.info("Processing SNOW ticket: %s | server_id=%s | type=%s | state=%s",
             ticket_number, server_id, request_type, state)

    verdict_data = get_server_verdict(server_id)

    if not verdict_data:
        log.warning("Server %s not found in registry. Ticket %s needs manual review.",
                    server_id, ticket_number)
        route_to_approval_workflow(server_id, ticket_number, {
            "verdict": "UNKNOWN",
            "trust_score": 0.0,
            "risk_tier": "HIGH_RISK"
        })
        update_snow_ticket(oauth_token, ticket_sys_id, {
            "u_state": "needs_manual_review",
            "u_review_notes": f"Server {server_id} not found in registry. Needs analyst review."
        })
        log_audit_event(event_id, ticket_number, server_id, "server_not_found",
                        "system", "Server not in registry, routed to approval_workflow")
        return True

    verdict = verdict_data.get("verdict", "UNKNOWN")
    trust_score = verdict_data.get("trust_score", 0.0)
    risk_tier = verdict_data.get("risk_tier", "UNKNOWN")

    log.info("Ticket %s verdict: %s (score=%.2f, tier=%s)",
             ticket_number, verdict, trust_score, risk_tier)

    if verdict in ("TRUSTED", "ENTERPRISE_CONTROLLED"):
        snow_data = {
            "u_state": "approved",
            "u_approved": "true",
            "u_review_notes": f"Auto-approved: verdict={verdict}, trust_score={trust_score:.2f}"
        }
        update_snow_ticket(oauth_token, ticket_sys_id, snow_data)
        log_audit_event(event_id, ticket_number, server_id, "auto_approved",
                        "system", f"Verdict={verdict}, score={trust_score:.2f}")
        log.info("Ticket %s auto-approved.", ticket_number)
        return True

    if verdict in ("UNTRUSTED", "KNOWN_THREAT"):
        snow_data = {
            "u_state": "rejected",
            "u_approved": "false",
            "u_review_notes": f"Auto-rejected: verdict={verdict}, trust_score={trust_score:.2f}"
        }
        update_snow_ticket(oauth_token, ticket_sys_id, snow_data)
        log_audit_event(event_id, ticket_number, server_id, "auto_rejected",
                        "system", f"Verdict={verdict}, score={trust_score:.2f}")
        log.info("Ticket %s auto-rejected.", ticket_number)
        return True

    route_to_approval_workflow(server_id, ticket_number, verdict_data)
    log_audit_event(event_id, ticket_number, server_id, "routed_for_review",
                    "system", f"Verdict={verdict}, routed to approval_workflow")
    return True


def send_heartbeat() -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": "running",
        "meta": json.dumps({"snow_instance": SNOW_INSTANCE or "not_configured"})
    }
    ws_write("service_health", [row])


def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(POLL_SECS)


def run() -> None:
    log.info("Starting %s", SERVICE_NAME)
    check_single_instance()

    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    ensure_tables()
    log.info("SNOW approval workflow wiring initialized.")
    log.info("SNOW Instance: %s", SNOW_INSTANCE or "NOT CONFIGURED")
    log.info("OAuth configured: %s", "YES" if SNOW_CLIENT_ID else "NO")
    log.info("Webhook secret configured: %s", "YES" if SNOW_WEBHOOK_SECRET else "NO")

    last_token_refresh = 0
    oauth_token = None

    while True:
        try:
            current_time = time.time()
            if current_time - last_token_refresh > 3500 or not oauth_token:
                oauth_token = get_snow_oauth_token()
                if oauth_token:
                    last_token_refresh = current_time
                    log.info("SNOW OAuth token refreshed successfully.")
                else:
                    log.warning("Failed to obtain SNOW OAuth token, will retry.")

            if oauth_token:
                pending_tickets = get_pending_snow_tickets(oauth_token)
                if pending_tickets:
                    log.info("Found %d pending SNOW tickets.", len(pending_tickets))
                    for ticket in pending_tickets:
                        try:
                            process_snow_ticket(ticket, oauth_token)
                        except Exception as e:
                            log.error("Error processing ticket %s: %s",
                                      ticket.get("number", "unknown"), e)
                else:
                    log.debug("No pending SNOW tickets found.")

            send_heartbeat()
            time.sleep(POLL_SECS)

        except KeyboardInterrupt:
            log.info("Received keyboard interrupt, shutting down.")
            break
        except Exception as e:
            log.error("Unexpected error in main loop: %s", e)
            time.sleep(POLL_SECS)

    remove_pid_file()
    log.info("%s shutdown complete.", SERVICE_NAME)


if __name__ == "__main__":
    run()