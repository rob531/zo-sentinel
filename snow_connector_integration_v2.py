import os
import sys
import time
import json
import logging
import signal
import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Header, Request, Depends
from pydantic import BaseModel
import uvicorn

# --- Constants ---
SERVICE_NAME = "snow_connector_integration_v2"
PORT = 8778
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
APPROVAL_WORKFLOW_URL = "http://127.0.0.1:8780"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"
POLL_SECS = 60

# ServiceNow credentials from environment
SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "")
SNOW_CLIENT_ID = os.environ.get("SNOW_CLIENT_ID", "")
SNOW_CLIENT_SECRET = os.environ.get("SNOW_CLIENT_SECRET", "")
SNOW_USERNAME = os.environ.get("SNOW_USERNAME", "")
SNOW_PASSWORD = os.environ.get("SNOW_PASSWORD", "")
SNOW_OAUTH_TOKEN_URL = os.environ.get("SNOW_OAUTH_TOKEN_URL", "")
SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")

# Token cache for OAuth flow recovery
_cached_token: Optional[str] = None
_token_expires_at: float = 0

# Prompt injection patterns from plan
PROMPT_INJECTION_PLAN = "/home/workspace/zo_sentinel/PROMPT_INECTION_PLAN.md"
PROMPT_INJECTION_PATTERNS: List[str] = []
if os.path.exists(PROMPT_INJECTION_PLAN):
    with open(PROMPT_INJECTION_PLAN) as f:
        content = f.read()
        PROMPT_INJECTION_PATTERNS = [p for p in re.findall(r'`([^`]+)`', content) if len(p) > 10]

# --- Logger (FileHandler only for nohup daemons) ---
logger = logging.getLogger(__name__)
_log_handler = logging.FileHandler(LOG_FILE)
_log_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)

app = FastAPI()

# --- Pydantic Models ---
class SnowTicketSubmission(BaseModel):
    short_description: Optional[str] = ""
    description: Optional[str] = ""
    u_mcp_server_name: Optional[str] = ""
    u_requested_by: Optional[str] = ""
    ticket_id: Optional[str] = ""
    sys_id: Optional[str] = ""
    state: Optional[int] = None
    priority: Optional[int] = None


class SnowWebhookPayload(BaseModel):
    ticket_number: Optional[str] = None
    sys_id: Optional[str] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    u_mcp_server_name: Optional[str] = None
    u_requested_by: Optional[str] = None
    assigned_to: Optional[str] = None
    priority: Optional[str] = None
    u_approval_status: Optional[str] = None


# --- Utility Functions ---
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_for_log(text: Optional[str]) -> str:
    if not text:
        return "[EMPTY]"
    return f"[CONTENT_LENGTH:{len(text)}]"


def check_prompt_injection(text: Optional[str]) -> bool:
    if not text or not PROMPT_INJECTION_PATTERNS:
        return False
    text_lower = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.lower() in text_lower:
            logger.warning(f"Prompt injection pattern detected: {pattern[:30]}...")
            return True
    return False


def compute_ticket_hash(ticket_number: str, sys_id: str) -> str:
    return hashlib.sha256(f"{ticket_number}:{sys_id}".encode()).hexdigest()[:16]


# --- Write Service Helpers ---
def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write({table}) failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return False


# --- OAuth Token Management with Recovery ---
def is_token_fresh() -> bool:
    global _cached_token, _token_expires_at
    if not _cached_token:
        return False
    if time.time() >= _token_expires_at - 60:
        return False
    return True


def get_snow_oauth_token() -> Optional[str]:
    """Fetches a fresh ServiceNow OAuth token with error recovery for expired tokens."""
    global _cached_token, _token_expires_at

    if is_token_fresh():
        logger.debug("Using cached OAuth token")
        return _cached_token

    if not SNOW_INSTANCE or not SNOW_CLIENT_ID or not SNOW_CLIENT_SECRET:
        logger.warning("ServiceNow OAuth credentials not configured, falling back to basic auth")
        return None

    token_url = SNOW_OAUTH_TOKEN_URL or f"https://{SNOW_INSTANCE}.service-now.com/oauth_token.do"
    payload = {
        "grant_type": "client_credentials",
        "client_id": SNOW_CLIENT_ID,
        "client_secret": SNOW_CLIENT_SECRET,
    }

    try:
        resp = requests.post(token_url, data=payload, timeout=30)
        resp.raise_for_status()
        token_data = resp.json()
        _cached_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 3600)
        _token_expires_at = time.time() + expires_in
        logger.info(f"Obtained fresh ServiceNow OAuth token, expires in {expires_in}s")
        return _cached_token
    except requests.exceptions.HTTPError as e:
        logger.error(f"ServiceNow OAuth HTTP error: {e.response.status_code} - {e.response.text[:200]}")
        if e.response is not None and e.response.status_code == 401:
            logger.warning("OAuth token expired (401), invalidating cache and retrying once")
            _cached_token = None
            _token_expires_at = 0
            return get_snow_oauth_token_retry()
        return None
    except Exception as e:
        logger.error(f"ServiceNow OAuth token fetch failed: {e}")
        return None


def get_snow_oauth_token_retry() -> Optional[str]:
    """Retry OAuth flow after clearing expired token cache."""
    global _cached_token, _token_expires_at
    _cached_token = None
    _token_expires_at = 0
    return get_snow_oauth_token()


def invalidate_expired_token() -> None:
    """Called when ServiceNow returns 401 to force re-authentication."""
    global _cached_token, _token_expires_at
    logger.warning("Invalidating expired OAuth token cache")
    _cached_token = None
    _token_expires_at = 0


# --- ServiceNow API Helpers ---
def make_snow_request(
    method: str,
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    retry_on_401: bool = True,
) -> Optional[Dict[str, Any]]:
    """Generic ServiceNow API request with OAuth expiry recovery."""
    token = get_snow_oauth_token()
    if not token:
        logger.error("No valid OAuth token available for ServiceNow request")
        return None

    url = f"https://{SNOW_INSTANCE}.service-now.com/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        resp = requests.request(method, url, headers=headers, params=params, json=data, timeout=30)
        if resp.status_code == 401 and retry_on_401:
            logger.warning("Received 401 from ServiceNow, re-authenticating and retrying")
            invalidate_expired_token()
            token = get_snow_oauth_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.request(method, url, headers=headers, params=params, json=data, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"ServiceNow HTTP {e.response.status_code}: {e.response.text[:200]}")
        if e.response.status_code == 401:
            invalidate_expired_token()
        return None
    except Exception as e:
        logger.error(f"ServiceNow request failed: {e}")
        return None


def get_ticket_by_number(ticket_number: str) -> Optional[Dict[str, Any]]:
    """Fetch ServiceNow ticket by number."""
    result = make_snow_request(
        "GET",
        f"api/now/table/incident?number={ticket_number}&sysparm_limit=1",
    )
    if result and result.get("result"):
        return result["result"][0]
    return None


# --- Server Registry Lookup ---
def get_server_verdict(server_name: str) -> Optional[Dict[str, Any]]:
    sql = f"SELECT server_id, name, verdict, trust_score, risk_tier FROM mcp_server_registry WHERE name ILIKE '%{server_name}%' LIMIT 5"
    rows = ws_query(sql)
    for row in rows:
        if row.get("name", "").lower() == server_name.lower():
            return row
    return rows[0] if rows else None


# --- Submission Upsert with ON CONFLICT ---
def upsert_mcp_submission(
    ticket_number: str,
    sys_id: str,
    short_description: str,
    description: str,
    requested_by: str,
    mcp_server_name: str,
    state: int = 1,
    priority: int = 3,
) -> bool:
    """
    Upsert MCP submission record.
    ON CONFLICT DO UPDATE when a duplicate SNOW ticket arrives for the same
    mcp_server_name or ticket_number, updating state and description.
    """
    ts = utc_now_iso()
    ticket_hash = compute_ticket_hash(ticket_number, sys_id)

    sql = f"""
    INSERT INTO mcp_submissions (
        ticket_number,
        sys_id,
        short_description,
        description,
        requested_by,
        mcp_server_name,
        state,
        priority,
        ticket_hash,
        submitted_at,
        updated_at,
        source
    ) VALUES (
        '{ticket_number}',
        '{sys_id}',
        '{short_description.replace("'", "''")}',
        '{description.replace("'", "''")}',
        '{requested_by.replace("'", "''")}',
        '{mcp_server_name.replace("'", "''")}',
        {state},
        {priority},
        '{ticket_hash}',
        '{ts}',
        '{ts}',
        'snow_connector'
    )
    ON CONFLICT (ticket_number)
    DO UPDATE SET
        state = EXCLUDED.state,
        short_description = EXCLUDED.short_description,
        description = EXCLUDED.description,
        priority = EXCLUDED.priority,
        updated_at = EXCLUDED.updated_at,
        sys_id = COALESCE(mcp_submissions.sys_id, EXCLUDED.sys_id)
    """
    return ws_execute(sql)


# --- Exemption Upsert with valid_until ---
def upsert_exemption_from_snow(
    mcp_server_name: str,
    approved_by: str,
    reason: str,
    exemption_days: int = 90,
) -> bool:
    """
    Create or update an exemption record for an approved SNOW ticket.
    Sets valid_until to now + exemption_days so expiry_manager can act on it.
    """
    ts = utc_now_iso()
    valid_until = (datetime.now(timezone.utc) + timedelta(days=exemption_days)).isoformat().replace("+00:00", "Z")
    exemption_hash = hashlib.sha256(f"{mcp_server_name}:snow:{approved_by}".encode()).hexdigest()[:32]

    sql = f"""
    INSERT INTO exemption_records (
        server_id,
        exemption_type,
        granted_by,
        granted_at,
        valid_until,
        reason,
        auto_exempt,
        exemption_hash,
        status
    ) VALUES (
        (SELECT server_id FROM mcp_server_registry WHERE name ILIKE '%{mcp_server_name}%' LIMIT 1),
        'snow_approval',
        '{approved_by.replace("'", "''")}',
        '{ts}',
        '{valid_until}',
        '{reason.replace("'", "''")}',
        true,
        '{exemption_hash}',
        'active'
    )
    ON CONFLICT (exemption_hash)
    DO UPDATE SET
        valid_until = EXCLUDED.valid_until,
        granted_at = EXCLUDED.granted_at,
        status = 'active'
    """
    return ws_execute(sql)


# --- Audit Log ---
def log_snow_event(
    event_type: str,
    ticket_number: str,
    detail: str,
    severity: str = "INFO",
) -> bool:
    ts = utc_now_iso()
    sql = f"""
    INSERT INTO audit_log (
        id,
        target_server_id,
        event_type,
        actor,
        detail,
        created_at
    ) VALUES (
        '{hashlib.sha256((ticket_number + ts).encode()).hexdigest()[:32]}',
        (SELECT server_id FROM mcp_server_registry WHERE name ILIKE '%{detail.split()[0]}%' LIMIT 1),
        '{event_type}',
        'snow_connector',
        '{detail.replace("'", "''")[:500]}',
        '{ts}'
    )
    """
    return ws_execute(sql)


# --- Approval Workflow Proxy ---
def forward_to_approval_workflow(submission_data: Dict[str, Any]) -> bool:
    """Forward approved ticket data to the approval_workflow API on port 8780."""
    try:
        payload = {
            "ticket_number": submission_data.get("ticket_number"),
            "sys_id": submission_data.get("sys_id"),
            "short_description": submission_data.get("short_description", ""),
            "description": submission_data.get("description", ""),
            "u_mcp_server_name": submission_data.get("mcp_server_name", ""),
            "u_requested_by": submission_data.get("requested_by", ""),
            "source": "snow_webhook",
            "verdict": submission_data.get("verdict", "AMBER_UNVERIFIED"),
            "trust_score": submission_data.get("trust_score", 50),
        }
        resp = requests.post(
            f"{APPROVAL_WORKFLOW_URL}/api/submissions",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        logger.info(f"Forwarded ticket {submission_data.get('ticket_number')} to approval_workflow")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"approval_workflow proxy HTTP {e.response.status_code}: {e.response.text[:200]}")
        return False
    except Exception as e:
        logger.error(f"approval_workflow proxy failed: {e}")
        return False


# --- Ensure Tables ---
def ensure_tables() -> None:
    """Create required tables if they do not exist."""
    tables = [
        """
        CREATE TABLE IF NOT EXISTS exemption_records (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            server_id VARCHAR,
            exemption_type VARCHAR,
            granted_by VARCHAR,
            granted_at TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            reason TEXT,
            auto_exempt BOOLEAN DEFAULT false,
            exemption_hash VARCHAR UNIQUE,
            status VARCHAR DEFAULT 'active'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mcp_submissions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ticket_number VARCHAR UNIQUE,
            sys_id VARCHAR,
            short_description VARCHAR,
            description TEXT,
            requested_by VARCHAR,
            mcp_server_name VARCHAR,
            state INTEGER DEFAULT 1,
            priority INTEGER DEFAULT 3,
            ticket_hash VARCHAR,
            submitted_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ,
            source VARCHAR DEFAULT 'snow_connector'
        )
        """,
    ]
    for sql in tables:
        ws_execute(sql)


# --- Single Instance Guard ---
def check_single_instance() -> bool:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            logger.error(f"Already running as PID {old_pid}, exiting")
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            logger.warning(f"Stale PID file {PID_FILE}, will overwrite")
    pid_file.write_text(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum: int, frame) -> None:
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


# --- Health Check ---
@app.get("/health")
async def health():
    global _cached_token, _token_expires_at
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "oauth_token_cached": _cached_token is not None,
        "oauth_expires_at": _token_expires_at,
        "snow_instance": SNOW_INSTANCE or "[not configured]",
    }


# --- Webhook Endpoint ---
@app.post("/webhook/snow")
async def snow_webhook(request: Request, payload: Optional[SnowWebhookPayload] = None):
    if SNOW_WEBHOOK_SECRET:
        sig = request.headers.get("X-SNOW-Signature", "")
        if not verify_webhook_signature(sig, request):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if payload is None:
        body = await request.json()
        payload = SnowWebhookPayload(**body)

    ticket_number = payload.ticket_number or ""
    sys_id = payload.sys_id or ""
    short_description = payload.short_description or ""
    description = payload.description or ""
    mcp_server_name = payload.u_mcp_server_name or short_description
    requested_by = payload.u_requested_by or ""
    approval_status = payload.u_approval_status or ""

    logger.info(f"Received SNOW webhook: ticket={ticket_number} state={payload.state} approval={approval_status}")

    if check_prompt_injection(short_description) or check_prompt_injection(description):
        log_snow_event("snow_rejected", ticket_number, "REJECTED: prompt injection detected")
        raise HTTPException(status_code=400, detail="Content rejected: potential prompt injection")

    state_map = {"1": 1, "2": 2, "3": 3, "4": 4, "resolved": 4, "closed": 5}
    state_int = state_map.get(str(payload.state), 1)

    if approval_status.lower() in ("approved", "approve", "yes"):
        verdict_data = get_server_verdict(mcp_server_name)
        upsert_mcp_submission(
            ticket_number=ticket_number,
            sys_id=sys_id,
            short_description=short_description,
            description=description,
            requested_by=requested_by,
            mcp_server_name=mcp_server_name,
            state=state_int,
            priority=int(payload.priority or 3),
        )
        upsert_exemption_from_snow(
            mcp_server_name=mcp_server_name,
            approved_by=requested_by,
            reason=f"ServiceNow approved ticket {ticket_number}",
            exemption_days=90,
        )
        log_snow_event("snow_approved", ticket_number, f"MCP:{mcp_server_name} approved via SNOW ticket")
        forward_to_approval_workflow({
            "ticket_number": ticket_number,
            "sys_id": sys_id,
            "short_description": short_description,
            "description": description,
            "mcp_server_name": mcp_server_name,
            "requested_by": requested_by,
            "verdict": verdict_data.get("verdict") if verdict_data else "AMBER_UNVERIFIED",
            "trust_score": verdict_data.get("trust_score") if verdict_data else 50,
        })
        logger.info(f"Ticket {ticket_number} processed as approved")
    elif approval_status.lower() in ("rejected", "reject", "no"):
        upsert_mcp_submission(
            ticket_number=ticket_number,
            sys_id=sys_id,
            short_description=short_description,
            description=description,
            requested_by=requested_by,
            mcp_server_name=mcp_server_name,
            state=state_int,
            priority=int(payload.priority or 3),
        )
        log_snow_event("snow_rejected", ticket_number, f"MCP:{mcp_server_name} rejected via SNOW ticket")
        logger.info(f"Ticket {ticket_number} processed as rejected")
    else:
        upsert_mcp_submission(
            ticket_number=ticket_number,
            sys_id=sys_id,
            short_description=short_description,
            description=description,
            requested_by=requested_by,
            mcp_server_name=mcp_server_name,
            state=state_int,
            priority=int(payload.priority or 3),
        )
        logger.info(f"Ticket {ticket_number} upserted (state={state_int})")

    return {"status": "received", "ticket_number": ticket_number}


def verify_webhook_signature(signature: str, request: Request) -> bool:
    if not SNOW_WEBHOOK_SECRET:
        return True
    try:
        body = request._content
        if isinstance(body, bytes):
            body = body.decode()
        if not isinstance(body, str):
            body = json.dumps(body)
        expected = hashlib.sha256((body + SNOW_WEBHOOK_SECRET).encode()).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception as e:
        logger.error(f"Signature verification failed: {e}")
        return False


# --- Manual Ticket Sync (for recovery) ---
@app.post("/sync/ticket/{ticket_number}")
async def sync_ticket(ticket_number: str):
    """Manual sync endpoint to re-fetch and process a SNOW ticket by number."""
    ticket = get_ticket_by_number(ticket_number)
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_number} not found in ServiceNow")

    payload = SnowWebhookPayload(
        ticket_number=ticket.get("number"),
        sys_id=ticket.get("sys_id"),
        short_description=ticket.get("short_description", ""),
        description=ticket.get("description", ""),
        state=ticket.get("state"),
        u_mcp_server_name=ticket.get("u_mcp_server_name", ""),
        u_requested_by=ticket.get("u_requested_by", ""),
        u_approval_status=ticket.get("u_approval_status", ""),
        priority=ticket.get("priority"),
    )
    return await snow_webhook(Request(request.scope, request._receive), payload)


# --- Heartbeat ---
def send_heartbeat() -> None:
    ts = utc_now_iso()
    meta = {
        "snow_instance": SNOW_INSTANCE or "[not configured]",
        "oauth_cached": _cached_token is not None,
    }
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "running",
        "meta": json.dumps(meta),
    }])


# --- Main Run Loop ---
def run() -> None:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        sys.exit(1)

    logger.info(f"{SERVICE_NAME} starting on port {PORT}")
    ensure_tables()
    send_heartbeat()

    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT)
    except Exception as e:
        logger.error(f"Fatal: {e}")
        remove_pid_file()
        sys.exit(1)


if __name__ == "__main__":
    run()