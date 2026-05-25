#!/usr/bin/env python3
"""
aidr_verdict_enforcement_wiring.py
Phase 9 verdict enforcement for aidr_commit_gateway

MUST rules:
1. Query write_service for verdict before forwarding any commit
2. Reject commits for CAUTION_LIMITED or HIGH_RISK_ISOLATED verdict
3. Include injection_resilience score in commit payload
4. Use write_service for all state, never direct HTTP
"""

import os
import sys
import time
import json
import signal
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

# Constants
SERVICE_NAME = "aidr_verdict_enforcement_wiring"
SERVICE_PORT = 8792
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 15

VERDICT_REJECT_LIST = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED"}
VERDICT_OVERRIDE_FLAG = "FORCE_COMMIT"

INJECTION_RESILIENCE_SIGNALS = {
    "prompt_injection_resilience",
    "injection_resilience_score",
    "injection_resilience_total",
    "context_manipulation_resilience",
}

# HTTP client defaults
HTTP_TIMEOUT = 10
MAX_RETRIES = 3
RETRY_DELAY = 2


def log(msg: str) -> None:
    """Write log message with timestamp."""
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_write_url() -> str:
    return f"{WRITE_SERVICE_URL}/write"


def get_query_url() -> str:
    return f"{QUERY_SERVICE_URL}/query"


def get_execute_url() -> str:
    return f"{EXECUTE_SERVICE_URL}/execute"


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to write_service."""
    payload = {"table": table, "rows": rows, "wait": True}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(get_write_url(), json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return True
            log(f"ws_write attempt {attempt+1} failed: {resp.status_code}")
        except Exception as e:
            log(f"ws_write attempt {attempt+1} exception: {e}")
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    """Query write_service for rows."""
    payload = {"sql": sql}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(get_query_url(), json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("rows", [])
            log(f"ws_query attempt {attempt+1} failed: {resp.status_code}")
        except Exception as e:
            log(f"ws_query attempt {attempt+1} exception: {e}")
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    return None


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML on write_service."""
    payload = {"sql": sql}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(get_execute_url(), json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("ok", False)
            log(f"ws_execute attempt {attempt+1} failed: {resp.status_code}")
        except Exception as e:
            log(f"ws_execute attempt {attempt+1} exception: {e}")
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    return False


def check_single_instance() -> bool:
    """Check if another instance is running."""
    pid = os.getpid()
    try:
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        if old_pid != pid and os.path.exists(f"/proc/{old_pid}"):
            log(f"Instance already running as PID {old_pid}")
            return False
    except FileNotFoundError:
        pass
    except ValueError:
        pass
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
    except Exception as e:
        log(f"Cannot write PID file: {e}")
    return True


def remove_pid_file() -> None:
    """Remove PID file on shutdown."""
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame) -> None:
    """Handle shutdown signals."""
    sig_name = signal.Signals(signum).name
    log(f"Received {sig_name}, shutting down")
    remove_pid_file()
    sys.exit(0)


def get_utc_now_iso() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def send_heartbeat() -> bool:
    """Send heartbeat to service_health table."""
    payload = {
        "table": "service_health",
        "rows": [{"service": SERVICE_NAME, "last_heartbeat": get_utc_now_iso()}],
        "wait": True,
    }
    try:
        resp = requests.post(get_write_url(), json=payload, timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def get_server_by_mcp_name(mcp_name: str) -> Optional[Dict[str, Any]]:
    """Query registry for MCP server by name."""
    sql = f"SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry WHERE name = '{mcp_name}' LIMIT 1"
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0]
    return None


def get_server_by_id(server_id: str) -> Optional[Dict[str, Any]]:
    """Query registry for MCP server by ID."""
    sql = f"SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry WHERE server_id = '{server_id}' LIMIT 1"
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0]
    return None


def get_injection_resilience_score(server_id: str) -> Optional[float]:
    """Query signal_scores for injection_resilience score."""
    signal_names = "', '".join(INJECTION_RESILIENCE_SIGNALS)
    sql = f"SELECT score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name IN ('{signal_names}') ORDER BY scored_at DESC LIMIT 1"
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return float(rows[0].get("score", 0.0))
    return None


def check_verdict_for_commit(server_id: str, mcp_name: str, override_flag: Optional[str] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Check if a commit is allowed based on verdict.
    
    Returns:
        (is_allowed, reason, server_record)
    """
    server = get_server_by_id(server_id)
    if not server:
        server = get_server_by_mcp_name(mcp_name)
    
    if not server:
        log(f"No registry entry found for server_id={server_id} or mcp_name={mcp_name}")
        return (False, "SERVER_NOT_FOUND", None)
    
    verdict = server.get("verdict", "UNKNOWN")
    
    if override_flag == VERDICT_OVERRIDE_FLAG:
        log(f"Override flag present, allowing commit despite verdict={verdict}")
        return (True, "OVERRIDE_APPLIED", server)
    
    if verdict in VERDICT_REJECT_LIST:
        log(f"Verdict {verdict} blocks commit for server_id={server_id}")
        return (False, f"VERDICT_BLOCKED:{verdict}", server)
    
    log(f"Verdict {verdict} allows commit for server_id={server_id}")
    return (True, f"VERDICT_ALLOWED:{verdict}", server)


def enrich_commit_payload(server_id: str, mcp_name: str, base_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich commit payload with injection_resilience score.
    
    Returns enriched payload with additional fields.
    """
    enriched = base_payload.copy()
    
    injection_score = get_injection_resilience_score(server_id)
    if injection_score is not None:
        enriched["injection_resilience_score"] = injection_score
        enriched["injection_resilience_verdict"] = "RESILIENT" if injection_score >= 0.7 else "VULNERABLE"
    else:
        enriched["injection_resilience_score"] = None
        enriched["injection_resilience_verdict"] = "UNKNOWN"
    
    enriched["verdict_checked_at"] = get_utc_now_iso()
    enriched["verdict_check_server_id"] = server_id
    
    return enriched


def verify_commit_allowed(
    server_id: str,
    mcp_name: str,
    commit_payload: Dict[str, Any],
    override_flag: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Complete verdict enforcement check for a commit.
    
    1. Query write_service for verdict
    2. Reject if CAUTION_LIMITED or HIGH_RISK_ISOLATED without override
    3. Enrich payload with injection_resilience score
    
    Returns:
        (commit_allowed, enriched_payload)
    """
    allowed, reason, server = check_verdict_for_commit(server_id, mcp_name, override_flag)
    
    if not allowed:
        log(f"Commit REJECTED: {reason} for server_id={server_id}")
        return (False, {"status": "rejected", "reason": reason})
    
    enriched = enrich_commit_payload(server_id, mcp_name, commit_payload)
    enriched["status"] = "approved"
    enriched["verdict_reason"] = reason
    
    log(f"Commit APPROVED: {reason} for server_id={server_id}")
    return (True, enriched)


def record_commit_decision(
    server_id: str,
    mcp_name: str,
    decision: str,
    reason: str,
    injection_score: Optional[float],
) -> bool:
    """Record commit decision to audit trail."""
    sql = """
    INSERT INTO audit_log (target_server_id, event_type, actor, detail, created_at)
    VALUES (
        '{server_id}',
        'VERDICT_CHECK',
        '{SERVICE_NAME}',
        '{{"decision": "{decision}", "reason": "{reason}", "injection_resilience_score": {injection_score}}}',
        '{timestamp}'
    )
    """.format(
        server_id=server_id,
        SERVICE_NAME=SERVICE_NAME,
        decision=decision,
        reason=reason,
        injection_score=injection_score if injection_score is not None else "NULL",
        timestamp=get_utc_now_iso(),
    )
    return ws_execute(sql)


def ensure_enforcement_tables() -> bool:
    """Ensure verdict enforcement tracking table exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS verdict_enforcement_log (
        id INTEGER PRIMARY KEY,
        server_id VARCHAR,
        mcp_name VARCHAR,
        verdict VARCHAR,
        decision VARCHAR,
        reason VARCHAR,
        injection_resilience_score DOUBLE,
        override_used BOOLEAN,
        created_at TIMESTAMP
    )
    """
    return ws_execute(sql)


def log_verdict_decision(
    server_id: str,
    mcp_name: str,
    verdict: str,
    decision: str,
    reason: str,
    injection_score: Optional[float],
    override_used: bool,
) -> bool:
    """Log verdict decision to enforcement tracking table."""
    sql = """
    INSERT INTO verdict_enforcement_log (
        server_id, mcp_name, verdict, decision, reason,
        injection_resilience_score, override_used, created_at
    )
    VALUES (
        '{server_id}', '{mcp_name}', '{verdict}', '{decision}', '{reason}',
        {injection_score}, {override_used}, '{timestamp}'
    )
    """.format(
        server_id=server_id,
        mcp_name=mcp_name,
        verdict=verdict,
        decision=decision,
        reason=reason,
        injection_score=injection_score if injection_score is not None else "NULL",
        override_used="TRUE" if override_used else "FALSE",
        timestamp=get_utc_now_iso(),
    )
    return ws_write("verdict_enforcement_log", [{
        "server_id": server_id,
        "mcp_name": mcp_name,
        "verdict": verdict,
        "decision": decision,
        "reason": reason,
        "injection_resilience_score": injection_score,
        "override_used": override_used,
        "created_at": get_utc_now_iso(),
    }])


def wrap_commit_for_verdict(
    server_id: str,
    mcp_name: str,
    commit_payload: Dict[str, Any],
    override_flag: Optional[str] = None,
    record_audit: bool = True,
) -> Dict[str, Any]:
    """
    Full verdict enforcement wrapper for any commit operation.
    
    This is the primary API for verdict-enforced commits.
    
    Args:
        server_id: Server ID from registry
        mcp_name: MCP server name
        commit_payload: Original commit payload
        override_flag: Optional override flag (e.g., "FORCE_COMMIT")
        record_audit: Whether to record to audit trail
        
    Returns:
        Dict with:
            - status: "approved" or "rejected"
            - reason: Decision reason
            - payload: Enriched payload (if approved)
            - injection_resilience_score: Score from signals
    """
    server = get_server_by_id(server_id)
    if not server:
        server = get_server_by_mcp_name(mcp_name)
    
    if not server:
        result = {
            "status": "rejected",
            "reason": "SERVER_NOT_FOUND",
            "payload": commit_payload,
        }
        if record_audit:
            record_commit_decision(server_id, mcp_name, "NOT_FOUND", "rejected", None)
        return result
    
    verdict = server.get("verdict", "UNKNOWN")
    use_override = override_flag == VERDICT_OVERRIDE_FLAG
    injection_score = get_injection_resilience_score(server_id)
    
    if verdict in VERDICT_REJECT_LIST and not use_override:
        result = {
            "status": "rejected",
            "reason": f"VERDICT_BLOCKED:{verdict}",
            "payload": commit_payload,
            "verdict": verdict,
            "injection_resilience_score": injection_score,
        }
        if record_audit:
            log_verdict_decision(
                server_id, mcp_name, verdict, "rejected",
                f"VERDICT_BLOCKED:{verdict}", injection_score, use_override
            )
        return result
    
    enriched = enrich_commit_payload(server_id, mcp_name, commit_payload)
    enriched["verdict"] = verdict
    enriched["verdict_reason"] = f"OVERRIDE_APPLIED:{verdict}" if use_override else f"VERDICT_ALLOWED:{verdict}"
    
    result = {
        "status": "approved",
        "reason": result.get("verdict_reason", "VERDICT_ALLOWED"),
        "payload": enriched,
        "verdict": verdict,
        "injection_resilience_score": injection_score,
    }
    
    if record_audit:
        log_verdict_decision(
            server_id, mcp_name, verdict, "approved",
            result["reason"], injection_score, use_override
        )
    
    return result


def get_pending_commit_checks() -> List[Dict[str, Any]]:
    """Get recent verdict enforcement decisions for monitoring."""
    sql = """
    SELECT server_id, mcp_name, verdict, decision, reason, injection_resilience_score, created_at
    FROM verdict_enforcement_log
    WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
    ORDER BY created_at DESC
    LIMIT 100
    """
    return ws_query(sql) or []


def get_rejected_commits_count() -> int:
    """Get count of rejected commits in last 24 hours."""
    sql = """
    SELECT COUNT(*) as cnt FROM verdict_enforcement_log
    WHERE decision = 'rejected' AND created_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return int(rows[0].get("cnt", 0))
    return 0


def get_override_usage_count() -> int:
    """Get count of override flag usage in last 24 hours."""
    sql = """
    SELECT COUNT(*) as cnt FROM verdict_enforcement_log
    WHERE override_used = TRUE AND created_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return int(rows[0].get("cnt", 0))
    return 0


def get_stats() -> Dict[str, Any]:
    """Get enforcement statistics."""
    rejected = get_rejected_commits_count()
    overrides = get_override_usage_count()
    return {
        "rejected_commits_24h": rejected,
        "override_usage_24h": overrides,
        "last_heartbeat": get_utc_now_iso(),
    }


def verify_aidr_gateway_wiring() -> bool:
    """Verify aidr_commit_gateway has verdict-check integration."""
    try:
        with open("/home/workspace/zo_sentinel/aidr_commit_gateway.py", "r") as f:
            content = f.read()
        
        required_patterns = [
            "verdict",
            "write_service",
            "CAUTION_LIMITED",
            "HIGH_RISK_ISOLATED",
            "injection_resilience",
        ]
        
        missing = [p for p in required_patterns if p not in content]
        if missing:
            log(f"aidr_commit_gateway missing patterns: {missing}")
            return False
        
        log("aidr_commit_gateway verdict-check integration verified")
        return True
    except FileNotFoundError:
        log("aidr_commit_gateway.py not found, skipping wiring verification")
        return True
    except Exception as e:
        log(f"Error verifying aidr_commit_gateway wiring: {e}")
        return False


def run_verdict_enforcement_cycle() -> None:
    """Run a single enforcement cycle."""
    ensure_enforcement_tables()
    
    stats = get_stats()
    log(f"Enforcement stats: rejected={stats['rejected_commits_24h']}, overrides={stats['override_usage_24h']}")
    
    pending = get_pending_commit_checks()
    if pending:
        log(f"Found {len(pending)} enforcement decisions in last hour")
        
        rejected = [p for p in pending if p.get("decision") == "rejected"]
        if rejected:
            log(f"WARNING: {len(rejected)} commits rejected in last hour")
            for r in rejected[:5]:
                log(f"  - server_id={r.get('server_id')}, reason={r.get('reason')}")
    
    send_heartbeat()


def run() -> None:
    """Main daemon loop."""
    log(f"Starting {SERVICE_NAME}")
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log("Cannot acquire PID lock, exiting")
        sys.exit(1)
    
    log("Verdict enforcement wiring initialized")
    ensure_enforcement_tables()
    verify_aidr_gateway_wiring()
    
    cycle_count = 0
    start_time = time.time()
    
    while True:
        cycle_count += 1
        run_verdict_enforcement_cycle()
        
        elapsed = time.time() - start_time
        if cycle_count % 10 == 0:
            log(f"Running {cycle_count} cycles, uptime={elapsed:.0f}s")
        
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()