import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
import os
import json
import logging
import hashlib
import hmac
import time
import uuid
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Header, Request, Depends
from pydantic import BaseModel
import uvicorn

SERVICE_NAME = "snow_connector_integration"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
SNOW_WEBHOOK_SECRET = os.environ.get("SNOW_WEBHOOK_SECRET", "")
SNOW_OAUTH_TOKEN_URL = os.environ.get("SNOW_OAUTH_TOKEN_URL", "")
SNOW_OAUTH_CLIENT_ID = os.environ.get("SNOW_OAUTH_CLIENT_ID", "")
SNOW_OAUTH_CLIENT_SECRET = os.environ.get("SNOW_OAUTH_CLIENT_SECRET", "")
SNOW_INSTANCE = os.environ.get("SNOW_INSTANCE", "")
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

app = FastAPI()

# ── Workflow state constants ──────────────────────────────────────────────────
STATE_APPROVED = "APPROVED"
STATE_CONDITIONAL = "CONDITIONAL"
STATE_REJECTED = "REJECTED"
STATE_PENDING = "PENDING"
STATE_AUTO_REJECTED = "AUTO_REJECTED"

# ── Verdict constants ─────────────────────────────────────────────────────────
VERDICT_KNOWN_THREAT = "KNOWN_THREAT"
VERDICT_HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
VERDICT_CAUTION_LIMITED = "CAUTION_LIMITED"
VERDICT_AMBER_UNVERIFIED = "AMBER_UNVERIFIED"
VERDICT_TRUSTED_RESEARCH = "TRUSTED_RESEARCH"
VERDICT_ENTERPRISE_CONTROLLED = "ENTERPRISE_CONTROLLED"
VERDICT_UNKNOWN = "UNKNOWN"

# ── Auto-approve allowed verdicts ─────────────────────────────────────────────
AUTO_APPROVE_VERDICTS = {VERDICT_TRUSTED_RESEARCH, VERDICT_ENTERPRISE_CONTROLLED}

# ── Always block verdicts ─────────────────────────────────────────────────────
ALWAYS_BLOCK_VERDICTS = {VERDICT_KNOWN_THREAT, VERDICT_HIGH_RISK_ISOLATED, VERDICT_CAUTION_LIMITED}

# ── OAuth token cache ─────────────────────────────────────────────────────────
_oauth_token_cache: Dict[str, Any] = {}

# ── Pydantic models ───────────────────────────────────────────────────────────
class SnowWebhookPayload(BaseModel):
    short_description: str
    description: Optional[str] = ""
    u_mcp_server_name: Optional[str] = ""
    u_requested_by: Optional[str] = ""
    ticket_id: Optional[str] = ""
    sys_id: Optional[str] = ""
    u_verdict_override: Optional[str] = None
    u_analyst_override: Optional[str] = None

class TicketSubmission(BaseModel):
    short_description: str
    description: Optional[str] = ""
    mcp_server_name: Optional[str] = ""
    requested_by: Optional[str] = ""
    snow_ticket_id: Optional[str] = ""
    snow_sys_id: Optional[str] = ""

class VerdictDecision(BaseModel):
    decision: str  # APPROVED, CONDITIONAL, REJECTED, AUTO_REJECTED
    verdict: str
    risk_tier: Optional[str] = ""
    reasoning: Optional[str] = ""
    analyst_override: bool = False

# ── Write service helpers ─────────────────────────────────────────────────────
def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": row, "wait": True},
            timeout=15
        )
        if r.status_code != 200:
            log.error(f"ws_write {table} failed: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        log.error(f"ws_write {table} exception: {e}")
        return False

def ws_query(sql: str, limit: int = 100) -> list:
    try:
        r = requests.post(
            f"{QUERY_URL}",
            json={"sql": sql, "limit": limit},
            timeout=15
        )
        if r.status_code == 200:
            return r.json().get("rows", [])
        return []
    except Exception as e:
        log.error(f"ws_query exception: {e}")
        return []

def ws_execute(sql: str) -> bool:
    try:
        r = requests.post(
            f"{EXECUTE_URL}",
            json={"sql": sql},
            timeout=15
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_execute exception: {e}")
        return False

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ── Prompt injection detection ─────────────────────────────────────────────────
def check_prompt_injection(text: str) -> bool:
    if not text:
        return False
    plan_path = "/home/workspace/zo_sentinel/PROMPT_INJECTION_PLAN.md"
    patterns = []
    if os.path.exists(plan_path):
        with open(plan_path, 'r') as f:
            content = f.read()
            patterns = [p for p in re.findall(r'`([^`]+)`', content) if len(p) > 10]
    text_lower = text.lower()
    for pattern in patterns:
        if pattern.lower() in text_lower:
            return True
    return False

import re

# ── Single instance guard ──────────────────────────────────────────────────────
def check_single_instance() -> bool:
    pid = os.getpid()
    try:
        existing = open(PID_FILE, 'r').read().strip()
        if existing and int(existing) == pid:
            return True
        open(PID_FILE, 'w').write(str(pid))
    except FileNotFoundError:
        open(PID_FILE, 'w').write(str(pid))
    except Exception:
        pass
    return True

def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

# ── OAuth helpers ──────────────────────────────────────────────────────────────
def is_token_fresh() -> bool:
    cached = _oauth_token_cache
    if not cached:
        return False
    expires_at = cached.get("expires_at", 0)
    return time.time() < (expires_at - 60)

def get_snow_oauth_token() -> Optional[str]:
    if is_token_fresh():
        return _oauth_token_cache.get("access_token")

    if not SNOW_OAUTH_TOKEN_URL or not SNOW_OAUTH_CLIENT_ID or not SNOW_OAUTH_CLIENT_SECRET:
        log.warning("SNOW OAuth credentials not configured; returning None")
        return None

    try:
        resp = requests.post(
            SNOW_OAUTH_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": SNOW_OAUTH_CLIENT_ID,
                "client_secret": SNOW_OAUTH_CLIENT_SECRET
            },
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            _oauth_token_cache["access_token"] = data.get("access_token")
            _oauth_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
            return data.get("access_token")
    except Exception as e:
        log.error(f"SNOW OAuth token fetch failed: {e}")
    return None

def sanitize_for_log(text: str) -> str:
    if not text:
        return "[EMPTY]"
    return f"[CONTENT_LENGTH:{len(text)}]"

# ── Webhook signature validation ───────────────────────────────────────────────
def verify_snow_webhook_signature(body: bytes, signature: str) -> bool:
    if not SNOW_WEBHOOK_SECRET or not signature:
        log.warning("SNOW webhook secret or signature missing; skipping validation")
        return True
    expected = hmac.new(
        SNOW_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

# ── SNOW outbound: update ticket ───────────────────────────────────────────────
def make_snow_request(method: str, path: str, data: Optional[dict] = None) -> Optional[dict]:
    token = get_snow_oauth_token()
    if not token:
        log.error("No SNOW OAuth token available")
        return None
    base = f"https://{SNOW_INSTANCE}" if SNOW_INSTANCE else "https://dev.service-now.com"
    url = f"{base}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=15)
        elif method == "PATCH":
            r = requests.patch(url, json=data, headers=headers, timeout=15)
        else:
            return None
        if r.status_code in (200, 201):
            return r.json() if r.text else {}
        log.error(f"SNOW API {method} {path} returned {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"SNOW API exception: {e}")
    return None

def update_snow_ticket(sys_id: str, fields: dict) -> bool:
    result = make_snow_request("PATCH", f"/api/now/table/incident/{sys_id}", fields)
    return result is not None

# ── Verdict lookup ─────────────────────────────────────────────────────────────
def get_server_verdict(server_name: str) -> Optional[dict]:
    sql = f"""
    SELECT server_id, name, verdict, trust_score, risk_tier
    FROM mcp_server_registry
    WHERE name ILIKE '%{server_name}%'
    LIMIT 1
    """
    rows = ws_query(sql, limit=5)
    for row in rows:
        if server_name.lower() in str(row.get("name", "")).lower():
            return {
                "verdict": row.get("verdict", VERDICT_UNKNOWN),
                "trust_score": row.get("trust_score", 0.0),
                "risk_tier": row.get("risk_tier", ""),
                "server_id": row.get("server_id", "")
            }
    if rows:
        return {
            "verdict": rows[0].get("verdict", VERDICT_UNKNOWN),
            "trust_score": rows[0].get("trust_score", 0.0),
            "risk_tier": rows[0].get("risk_tier", ""),
            "server_id": rows[0].get("server_id", "")
        }
    return None

def get_server_verdict_by_id(server_id: str) -> Optional[dict]:
    sql = f"SELECT server_id, name, verdict, trust_score, risk_tier FROM mcp_server_registry WHERE server_id = '{server_id}' LIMIT 1"
    rows = ws_query(sql, limit=1)
    if rows:
        row = rows[0]
        return {
            "verdict": row.get("verdict", VERDICT_UNKNOWN),
            "trust_score": row.get("trust_score", 0.0),
            "risk_tier": row.get("risk_tier", ""),
            "server_id": row.get("server_id", "")
        }
    return None

# ── Decision logic ─────────────────────────────────────────────────────────────
def compute_verdict_decision(
    verdict: str,
    risk_tier: str,
    analyst_override: bool = False,
    override_verdict: Optional[str] = None
) -> VerdictDecision:
    if override_verdict and analyst_override:
        if override_verdict == STATE_APPROVED:
            return VerdictDecision(
                decision=STATE_APPROVED,
                verdict=verdict,
                risk_tier=risk_tier,
                reasoning="Analyst override approved",
                analyst_override=True
            )
        elif override_verdict == STATE_REJECTED:
            return VerdictDecision(
                decision=STATE_REJECTED,
                verdict=verdict,
                risk_tier=risk_tier,
                reasoning="Analyst override rejected",
                analyst_override=True
            )
        elif override_verdict == STATE_CONDITIONAL:
            return VerdictDecision(
                decision=STATE_CONDITIONAL,
                verdict=verdict,
                risk_tier=risk_tier,
                reasoning="Analyst override conditional",
                analyst_override=True
            )

    if verdict in ALWAYS_BLOCK_VERDICTS:
        return VerdictDecision(
            decision=STATE_AUTO_REJECTED,
            verdict=verdict,
            risk_tier=risk_tier,
            reasoning=f"Auto-rejected: verdict={verdict}",
            analyst_override=False
        )

    if verdict in AUTO_APPROVE_VERDICTS:
        return VerdictDecision(
            decision=STATE_APPROVED,
            verdict=verdict,
            risk_tier=risk_tier,
            reasoning=f"Auto-approved: verdict={verdict}",
            analyst_override=False
        )

    if verdict in (VERDICT_AMBER_UNVERIFIED, VERDICT_UNKNOWN):
        return VerdictDecision(
            decision=STATE_PENDING,
            verdict=verdict,
            risk_tier=risk_tier,
            reasoning=f"Pending manual review: verdict={verdict}",
            analyst_override=False
        )

    return VerdictDecision(
        decision=STATE_CONDITIONAL,
        verdict=verdict,
        risk_tier=risk_tier,
        reasoning=f"Conditional: verdict={verdict}",
        analyst_override=False
    )

# ── Audit log writer ────────────────────────────────────────────────────────────
def write_audit_entry(
    snow_ticket_id: str,
    snow_sys_id: str,
    mcp_server_name: str,
    event_type: str,
    verdict: str,
    decision: str,
    reasoning: str,
    actor: str = "snow_connector_integration"
) -> bool:
    event_id = hashlib.sha256(
        f"{snow_ticket_id}:{snow_sys_id}:{event_type}:{utc_now_iso()}".encode()
    ).hexdigest()[:32]
    row = {
        "id": event_id,
        "target_server_id": mcp_server_name,
        "event_type": event_type,
        "actor": actor,
        "detail": json.dumps({
            "snow_ticket_id": snow_ticket_id,
            "snow_sys_id": snow_sys_id,
            "verdict": verdict,
            "decision": decision,
            "reasoning": reasoning,
            "ts": utc_now_iso()
        }),
        "created_at": utc_now_iso()
    }
    return ws_write("audit_log", row)

# ── Approval submission writer ───────────────────────────────────────────────────
def upsert_approval_submission(
    submission_id: str,
    snow_ticket_id: str,
    snow_sys_id: str,
    mcp_server_name: str,
    short_description: str,
    description: str,
    requested_by: str,
    decision: str,
    verdict: str,
    risk_tier: str,
    reasoning: str
) -> bool:
    existing_sql = f"SELECT id FROM approval_submissions WHERE submission_id = '{submission_id}' LIMIT 1"
    rows = ws_query(existing_sql, limit=1)
    now = utc_now_iso()
    if rows:
        update_sql = f"""
        UPDATE approval_submissions SET
            decision = '{decision}',
            verdict = '{verdict}',
            risk_tier = '{risk_tier}',
            reasoning = '{reasoning}',
            updated_at = '{now}'
        WHERE submission_id = '{submission_id}'
        """
        return ws_execute(update_sql)
    row = {
        "id": submission_id,
        "submission_id": submission_id,
        "mcp_server_name": mcp_server_name,
        "short_description": short_description,
        "description": description[:2000] if description else "",
        "requested_by": requested_by,
        "decision": decision,
        "verdict": verdict,
        "risk_tier": risk_tier,
        "reasoning": reasoning,
        "source": "snow_connector",
        "snow_ticket_id": snow_ticket_id,
        "snow_sys_id": snow_sys_id,
        "created_at": now,
        "updated_at": now
    }
    return ws_write("approval_submissions", row)

# ── Inbound webhook endpoint ────────────────────────────────────────────────────
@app.post("/api/snow/inbound")
async def snow_inbound_webhook(
    request: Request,
    x_snow_signature: Optional[str] = Header(None),
    x_snow_notification_id: Optional[str] = Header(None)
):
    body = await request.body()

    if not verify_snow_webhook_signature(body, x_snow_signature or ""):
        log.warning("SNOW webhook signature validation FAILED")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except Exception as e:
        log.error(f"Failed to parse SNOW webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    short_desc = payload.get("short_description", "")
    desc = payload.get("description", "")
    mcp_name = payload.get("u_mcp_server_name", "") or payload.get("mcp_server_name", "")
    requested_by = payload.get("u_requested_by", "") or payload.get("requested_by", "snow_user")
    ticket_id = payload.get("ticket_id", "")
    sys_id = payload.get("sys_id", "")
    override_verdict = payload.get("u_verdict_override")
    analyst_override = bool(payload.get("u_analyst_override"))

    if check_prompt_injection(short_desc) or check_prompt_injection(desc):
        log.warning(f"Prompt injection detected in SNOW ticket {ticket_id}")
        write_audit_entry(
            snow_ticket_id=ticket_id,
            snow_sys_id=sys_id,
            mcp_server_name=mcp_name,
            event_type="PROMPT_INJECTION_BLOCKED",
            verdict="N/A",
            decision="BLOCKED",
            reasoning="Prompt injection pattern detected in SNOW ticket"
        )
        raise HTTPException(status_code=400, detail="Prompt injection detected")

    log.info(f"Processing SNOW inbound ticket: {ticket_id} sys_id={sys_id} mcp={mcp_name}")

    submission_id = f"snow_{sys_id}" if sys_id else f"snow_{uuid.uuid4().hex[:16]}"

    if mcp_name:
        verdict_data = get_server_verdict(mcp_name)
        if not verdict_data:
            log.warning(f"Server '{mcp_name}' not found in registry; defaulting to UNKNOWN")
            verdict_data = {"verdict": VERDICT_UNKNOWN, "trust_score": 0.0, "risk_tier": "", "server_id": ""}

        decision_obj = compute_verdict_decision(
            verdict=verdict_data["verdict"],
            risk_tier=verdict_data["risk_tier"],
            analyst_override=analyst_override,
            override_verdict=override_verdict
        )

        log.info(f"Decision for {mcp_name}: verdict={verdict_data['verdict']} decision={decision_obj.decision}")

    else:
        verdict_data = {"verdict": VERDICT_UNKNOWN, "trust_score": 0.0, "risk_tier": "", "server_id": ""}
        decision_obj = VerdictDecision(
            decision=STATE_PENDING,
            verdict=VERDICT_UNKNOWN,
            risk_tier="",
            reasoning="No MCP server name in SNOW ticket; pending manual triage"
        )

    upsert_approval_submission(
        submission_id=submission_id,
        snow_ticket_id=ticket_id,
        snow_sys_id=sys_id,
        mcp_server_name=mcp_name,
        short_description=short_desc,
        description=desc,
        requested_by=requested_by,
        decision=decision_obj.decision,
        verdict=verdict_data["verdict"],
        risk_tier=verdict_data["risk_tier"],
        reasoning=decision_obj.reasoning
    )

    write_audit_entry(
        snow_ticket_id=ticket_id,
        snow_sys_id=sys_id,
        mcp_server_name=mcp_name,
        event_type="SNOW_INBOUND_TICKET",
        verdict=verdict_data["verdict"],
        decision=decision_obj.decision,
        reasoning=decision_obj.reasoning,
        actor=requested_by
    )

    if sys_id and decision_obj.decision in (STATE_APPROVED, STATE_AUTO_REJECTED):
        sn_fields = {
            "u_sentinel_decision": decision_obj.decision,
            "u_sentinel_verdict": verdict_data["verdict"],
            "u_sentinel_reasoning": decision_obj.reasoning[:500],
            "u_sentinel_processed_at": utc_now_iso()
        }
        if decision_obj.analyst_override:
            sn_fields["u_analyst_override"] = "true"
        update_snow_ticket(sys_id, sn_fields)

    return {
        "status": "processed",
        "submission_id": submission_id,
        "decision": decision_obj.decision,
        "verdict": verdict_data["verdict"],
        "reasoning": decision_obj.reasoning,
        "mcp_server_name": mcp_name,
        "snow_ticket_id": ticket_id
    }

# ── Manual decision from SNOW analyst ─────────────────────────────────────────
@app.post("/api/snow/decision/{submission_id}")
async def snow_decision(submission_id: str, decision: str = "", override_verdict: str = ""):
    if decision not in (STATE_APPROVED, STATE_CONDITIONAL, STATE_REJECTED):
        raise HTTPException(status_code=400, detail="Invalid decision value")

    sql = f"SELECT * FROM approval_submissions WHERE submission_id = '{submission_id}' LIMIT 1"
    rows = ws_query(sql, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Submission not found")

    row = rows[0]
    mcp_name = row.get("mcp_server_name", "")
    snow_ticket_id = row.get("snow_ticket_id", "")
    snow_sys_id = row.get("snow_sys_id", "")
    current_verdict = row.get("verdict", VERDICT_UNKNOWN)

    sql_update = f"""
    UPDATE approval_submissions SET
        decision = '{decision}',
        reasoning = 'Analyst decision: {decision}', updated_at = '{utc_now_iso()}'
    WHERE submission_id = '{submission_id}'
    """
    ws_execute(sql_update)

    write_audit_entry(
        snow_ticket_id=snow_ticket_id,
        snow_sys_id=snow_sys_id,
        mcp_server_name=mcp_name,
        event_type="SNOW_ANALYST_DECISION",
        verdict=current_verdict,
        decision=decision,
        reasoning=f"Analyst manually set decision to {decision}",
        actor="snow_analyst"
    )

    return {"status": "updated", "submission_id": submission_id, "decision": decision}

# ── Health + heartbeat ───────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "ts": utc_now_iso()}

def send_heartbeat():
    row = {"service": SERVICE_NAME, "status": "running", "last_heartbeat": utc_now_iso(), "meta": "{}"}
    ws_write("service_health", row)

def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(60)

# ── Startup: ensure audit_log table ────────────────────────────────────────────
def ensure_tables():
    ws_execute("""
    CREATE TABLE IF NOT EXISTS approval_submissions (
        id TEXT PRIMARY KEY,
        submission_id TEXT,
        mcp_server_name TEXT,
        short_description TEXT,
        description TEXT,
        requested_by TEXT,
        decision TEXT,
        verdict TEXT,
        risk_tier TEXT,
        reasoning TEXT,
        source TEXT,
        snow_ticket_id TEXT,
        snow_sys_id TEXT,
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ
    )
    """)
    ws_execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY,
        target_server_id TEXT,
        event_type TEXT,
        actor TEXT,
        detail TEXT,
        created_at TIMESTAMPTZ
    )
    """)

# ── Run ────────────────────────────────────────────────────────────────────────
def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    ensure_tables()
    import threading
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

if __name__ == "__main__":
    run()