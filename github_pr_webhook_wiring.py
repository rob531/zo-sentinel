#!/usr/bin/env python3
"""
github_pr_webhook_wiring.py -- ZO-SENTINEL Phase 9
GitHub PR Webhook Wiring Daemon.

Wires github_pr_checker.py into approval_workflow for GitHub PR verdict gating.
Monitors MCPs with pending approval_workflow status, fetches PR data via
github_pr_checker, and writes verdict results to mcp_server_registry via write_service.

Port: 8778 (assigned)
All state exchange via write_service on port 8772.
"""
import os
import sys
import time
import json
import logging
import requests
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

log = logging.getLogger(__name__)

# ── Service Constants ──────────────────────────────────────────────────────────
SERVICE_NAME = "github_pr_webhook_wiring"
PORT = 8778
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
POLL_SECS = 30
HEARTBEAT_INTERVAL = 20

# ── GitHub PR Checker Interface ─────────────────────────────────────────────────
# Import github_pr_checker as the sole GitHub interface
sys.path.insert(0, '/home/workspace/zo_sentinel')
try:
    from github_pr_checker import (
        ws_query as gh_ws_query,
        get_github_headers,
        parse_pr_url,
        fetch_pr_diff,
        fetch_pr_files,
        lookup_mcp_in_registry,
        compute_risk_tier,
        score_to_verdict,
        VERDICT_EMOJI,
        RISK_TIER_THRESHOLDS,
    )
    HAS_GITHUB_PR_CHECKER = True
except ImportError:
    HAS_GITHUB_PR_CHECKER = False
    log.warning("github_pr_checker.py not available; using built-in fallback")

# ── PID / Signal Handling ────────────────────────────────────────────────────────
def check_single_instance() -> bool:
    """Ensure only one instance of this service runs."""
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            return False
        except OSError:
            log.info(f"Stale PID file found (PID {old_pid}); removing")
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))
    return True

def remove_pid_file():
    """Remove PID file on shutdown."""
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}; shutting down")
    remove_pid_file()
    sys.exit(0)

# ── Write Service Helpers ───────────────────────────────────────────────────────
def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write a single row to write_service using 'rows' key."""
    try:
        r = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception as e:
        log.error(f"ws_write error to {table}: {e}")
        return False

def ws_query(sql: str, limit: int = 500) -> List[Dict[str, Any]]:
    """Query the write_service query endpoint."""
    try:
        r = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json={"sql": sql, "limit": limit},
            timeout=30
        )
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []

def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service execute endpoint."""
    try:
        r = requests.post(
            f"{EXECUTE_SERVICE_URL}/execute",
            json={"sql": sql},
            timeout=30
        )
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception as e:
        log.error(f"ws_execute error: {e}")
        return False

def send_heartbeat():
    """Send heartbeat to service_health table."""
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat()
                },
                "wait": True
            },
            timeout=10
        )
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")

# ── Database Schema ────────────────────────────────────────────────────────────
def ensure_tables():
    """Create necessary tables if they don't exist."""
    queries = [
        """
        CREATE TABLE IF NOT EXISTS github_pr_checks (
            server_id VARCHAR,
            pr_url VARCHAR,
            pr_number INTEGER,
            repository VARCHAR,
            verdict VARCHAR,
            risk_tier VARCHAR,
            trust_score_composite DOUBLE,
            findings JSON,
            checked_at VARCHAR,
            PRIMARY KEY (server_id, pr_url)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS github_pr_webhook_events (
            event_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            pr_url VARCHAR,
            event_type VARCHAR,
            payload JSON,
            processed_at VARCHAR
        )
        """,
    ]
    for sql in queries:
        ws_execute(sql)

# ── Trust Synthesiser Integration ─────────────────────────────────────────────
def get_verdict_composite(server_id: str) -> Optional[float]:
    """Fetch verdict_composite score from trust_synthesiser via write_service."""
    sql = f"""
        SELECT AVG(score) as avg_score
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        AND signal_name = 'verdict_composite'
        LIMIT 1
    """
    rows = ws_query(sql)
    if rows and rows[0].get("avg_score") is not None:
        return float(rows[0]["avg_score"])
    
    # Fallback: compute from all signals
    sql2 = f"""
        SELECT AVG(score) as trust_avg
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        AND score IS NOT NULL
        LIMIT 1
    """
    rows2 = ws_query(sql2)
    if rows2 and rows2[0].get("trust_avg") is not None:
        return float(rows2[0]["trust_avg"])
    return None

# ── MCP Registry Queries ───────────────────────────────────────────────────────
def get_pending_approval_mcps() -> List[Dict[str, Any]]:
    """Get MCPs that have approval_workflow status pending."""
    sql = """
        SELECT 
            server_id,
            name,
            url,
            approval_workflow_status,
            verdict,
            trust_score,
            github_repo,
            github_pr_url
        FROM mcp_server_registry
        WHERE approval_workflow_status IN ('pending', 'submitted', 'under_review', 'awaiting_verdict')
        OR (github_pr_url IS NOT NULL AND github_pr_url != '' AND (
            approval_workflow_status IS NULL OR 
            approval_workflow_status = ''
        ))
        LIMIT 100
    """
    return ws_query(sql)

def get_mcps_with_github_pr() -> List[Dict[str, Any]]:
    """Get MCPs that have GitHub PR URLs associated."""
    sql = """
        SELECT 
            server_id,
            name,
            url,
            github_repo,
            github_pr_url,
            approval_workflow_status,
            trust_score,
            verdict
        FROM mcp_server_registry
        WHERE github_pr_url IS NOT NULL 
        AND github_pr_url != ''
        LIMIT 100
    """
    return ws_query(sql)

# ── GitHub PR Checker Wrapper ───────────────────────────────────────────────────
def fetch_pr_data_via_checker(pr_url: str) -> Optional[Dict[str, Any]]:
    """Fetch PR data using github_pr_checker module."""
    if not HAS_GITHUB_PR_CHECKER:
        log.error("github_pr_checker not available")
        return None
    
    try:
        headers = get_github_headers()
        repo, pr_num = parse_pr_url(pr_url)
        if not repo or not pr_num:
            log.error(f"Failed to parse PR URL: {pr_url}")
            return None
        
        diff_data = fetch_pr_diff(repo, pr_num, headers)
        files_data = fetch_pr_files(repo, pr_num, headers)
        
        return {
            "pr_url": pr_url,
            "repository": repo,
            "pr_number": pr_num,
            "diff": diff_data,
            "files": files_data,
        }
    except Exception as e:
        log.error(f"Failed to fetch PR data for {pr_url}: {e}")
        return None

def assess_pr_safety(pr_data: Dict[str, Any], server_id: str) -> Dict[str, Any]:
    """Assess PR safety using github_pr_checker logic."""
    verdict_composite = get_verdict_composite(server_id) or 0.5
    
    findings = {
        "new_packages": [],
        "suspicious_patterns": [],
        "mcp_additions": [],
        "risk_indicators": [],
    }
    
    risk_tier = "caution"
    if verdict_composite >= RISK_TIER_THRESHOLDS.get("trusted", 0.7):
        risk_tier = "trusted"
    
    verdict = score_to_verdict(verdict_composite) if HAS_GITHUB_PR_CHECKER else "UNKNOWN"
    
    # Analyze diff and files
    if pr_data.get("diff"):
        diff_text = str(pr_data["diff"])
        if "package.json" in diff_text or "package-lock.json" in diff_text:
            findings["new_packages"].append("package.json modified")
        if "mcp" in diff_text.lower():
            findings["mcp_additions"].append("MCP-related changes detected")
    
    if pr_data.get("files"):
        for f in pr_data["files"]:
            fname = f.get("filename", "")
            if "mcp" in fname.lower():
                findings["mcp_additions"].append(f"MCP file: {fname}")
    
    # Build assessment
    assessment = {
        "verdict": verdict,
        "risk_tier": risk_tier,
        "trust_score_composite": verdict_composite,
        "findings": findings,
        "assessor_notes": f"PR assessed via github_pr_webhook_wiring. Trust score: {verdict_composite:.3f}",
    }
    
    return assessment

# ── PR Check Results Writer ─────────────────────────────────────────────────────
def write_pr_check_result(server_id: str, pr_url: str, assessment: Dict[str, Any]):
    """Write PR check result to github_pr_checks table."""
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "server_id": server_id,
        "pr_url": pr_url,
        "pr_number": assessment.get("pr_number"),
        "repository": assessment.get("repository"),
        "verdict": assessment.get("verdict"),
        "risk_tier": assessment.get("risk_tier"),
        "trust_score_composite": assessment.get("trust_score_composite"),
        "findings": json.dumps(assessment.get("findings", {})),
        "checked_at": now,
    }
    
    # Upsert using ON CONFLICT DO UPDATE (DuckDB-compatible)
    sql = f"""
        INSERT INTO github_pr_checks 
        (server_id, pr_url, pr_number, repository, verdict, risk_tier, 
         trust_score_composite, findings, checked_at)
        VALUES (
            '{server_id}',
            '{pr_url}',
            {row.get('pr_number') or 'NULL'},
            '{row.get('repository') or ''}',
            '{row.get('verdict') or ''}',
            '{row.get('risk_tier') or ''}',
            {row.get('trust_score_composite') or 'NULL'},
            '{json.dumps(assessment.get("findings", {}))}',
            '{now}'
        )
        ON CONFLICT (server_id, pr_url) DO UPDATE SET
            verdict = EXCLUDED.verdict,
            risk_tier = EXCLUDED.risk_tier,
            trust_score_composite = EXCLUDED.trust_score_composite,
            findings = EXCLUDED.findings,
            checked_at = EXCLUDED.checked_at
    """
    ws_execute(sql)

def update_mcp_registry_status(server_id: str, pr_url: str, assessment: Dict[str, Any]):
    """Update mcp_server_registry with PR check status and verdict."""
    verdict = assessment.get("verdict", "")
    risk_tier = assessment.get("risk_tier", "")
    trust_score = assessment.get("trust_score_composite")
    findings = assessment.get("findings", {})
    
    # Determine new approval status based on PR verdict
    new_status = "pending"
    if verdict in ("TRUSTED", "HIGH_RISK"):
        new_status = "verdict_ready"
    elif verdict == "CAUTION":
        new_status = "needs_review"
    
    sql = f"""
        UPDATE mcp_server_registry
        SET 
            approval_workflow_status = '{new_status}',
            verdict = '{verdict}',
            trust_score = {trust_score if trust_score else 'trust_score'},
            last_pr_check = '{datetime.now(timezone.utc).isoformat()}',
            pr_risk_tier = '{risk_tier}',
            pr_findings = '{json.dumps(findings)}'
        WHERE server_id = '{server_id}'
    """
    ws_execute(sql)

def record_webhook_event(server_id: str, pr_url: str, event_type: str, payload: Dict[str, Any]):
    """Record webhook event for audit trail."""
    import uuid
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    
    row = {
        "event_id": event_id,
        "server_id": server_id,
        "pr_url": pr_url,
        "event_type": event_type,
        "payload": json.dumps(payload),
        "processed_at": now,
    }
    
    ws_write("github_pr_webhook_events", row)

def record_audit_event(server_id: str, event_type: str, detail: str):
    """Record event to audit_log table."""
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "target_server_id": server_id,
        "event_type": event_type,
        "actor": SERVICE_NAME,
        "detail": detail,
        "created_at": now,
    }
    ws_write("audit_log", row)

# ── Main Processing Cycle ──────────────────────────────────────────────────────
def process_pending_prs():
    """Process MCPs with pending approval workflow that have PR URLs."""
    mcps = get_pending_approval_mcps()
    processed = 0
    
    for mcp in mcps:
        server_id = mcp.get("server_id")
        pr_url = mcp.get("github_pr_url")
        
        if not pr_url:
            continue
        
        log.info(f"Processing PR for {server_id}: {pr_url}")
        
        try:
            # Fetch PR data via github_pr_checker
            pr_data = fetch_pr_data_via_checker(pr_url)
            if not pr_data:
                log.warning(f"No PR data fetched for {pr_url}")
                continue
            
            # Assess PR safety
            assessment = assess_pr_safety(pr_data, server_id)
            
            # Include verdict_composite from trust_synthesiser
            verdict_composite = get_verdict_composite(server_id)
            assessment["trust_score_composite"] = verdict_composite or assessment.get("trust_score_composite", 0.5)
            
            # Write results
            write_pr_check_result(server_id, pr_url, assessment)
            update_mcp_registry_status(server_id, pr_url, assessment)
            
            # Record audit event
            detail = f"PR assessment complete: verdict={assessment.get('verdict')}, trust_score={assessment['trust_score_composite']:.3f}"
            record_audit_event(server_id, "pr_check_complete", detail)
            record_webhook_event(server_id, pr_url, "pr_check", assessment)
            
            processed += 1
            log.info(f"PR check complete for {server_id}: {assessment.get('verdict')}")
            
        except Exception as e:
            log.error(f"Error processing PR for {server_id}: {e}")
            record_audit_event(server_id, "pr_check_failed", str(e))
    
    return processed

def process_github_pr_mcps():
    """Process all MCPs with GitHub PR URLs regardless of approval status."""
    mcps = get_mcps_with_github_pr()
    processed = 0
    
    for mcp in mcps:
        server_id = mcp.get("server_id")
        pr_url = mcp.get("github_pr_url")
        
        if not pr_url:
            continue
        
        # Skip if already recently checked
        last_check = mcp.get("last_pr_check")
        if last_check:
            try:
                from datetime import datetime, timezone
                last_dt = datetime.fromisoformat(last_check)
                if (datetime.now(timezone.utc) - last_dt).total_seconds() < 3600:
                    continue
            except Exception:
                pass
        
        log.info(f"Re-checking PR for {server_id}: {pr_url}")
        
        try:
            pr_data = fetch_pr_data_via_checker(pr_url)
            if not pr_data:
                continue
            
            assessment = assess_pr_safety(pr_data, server_id)
            verdict_composite = get_verdict_composite(server_id)
            assessment["trust_score_composite"] = verdict_composite or 0.5
            
            write_pr_check_result(server_id, pr_url, assessment)
            processed += 1
            
        except Exception as e:
            log.error(f"Error re-checking PR for {server_id}: {e}")
    
    return processed

# ── FastAPI App ─────────────────────────────────────────────────────────────────
from fastapi import FastAPI, HTTPException
import uvicorn

app = FastAPI(title="GitHub PR Webhook Wiring", version="1.0.0")

@app.get("/health")
def health():
    """Service health endpoint."""
    return {"status": "ok", "service": SERVICE_NAME}

@app.get("/status")
def status():
    """Get current processing status."""
    pending = get_pending_approval_mcps()
    with_pr = get_mcps_with_github_pr()
    return {
        "service": SERVICE_NAME,
        "pending_approval_count": len(pending),
        "with_pr_url_count": len(with_pr),
    }

@app.post("/trigger/{server_id}")
def trigger_pr_check(server_id: str):
    """Manually trigger PR check for a specific server."""
    sql = f"SELECT server_id, github_pr_url FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")
    
    mcp = rows[0]
    pr_url = mcp.get("github_pr_url")
    if not pr_url:
        raise HTTPException(status_code=400, detail="No GitHub PR URL associated")
    
    pr_data = fetch_pr_data_via_checker(pr_url)
    if not pr_data:
        raise HTTPException(status_code=500, detail="Failed to fetch PR data")
    
    assessment = assess_pr_safety(pr_data, server_id)
    verdict_composite = get_verdict_composite(server_id)
    assessment["trust_score_composite"] = verdict_composite or 0.5
    
    write_pr_check_result(server_id, pr_url, assessment)
    update_mcp_registry_status(server_id, pr_url, assessment)
    record_audit_event(server_id, "manual_pr_check", f"Manual trigger: verdict={assessment.get('verdict')}")
    
    return {"status": "ok", "server_id": server_id, "assessment": assessment}

# ── Daemon Run Loop ─────────────────────────────────────────────────────────────
def run():
    """Main daemon run loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    
    if not check_single_instance():
        log.error("Failed to acquire PID lock")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    
    # Ensure tables exist
    ensure_tables()
    
    # Send initial heartbeat
    send_heartbeat()
    
    start_time = time.time()
    heartbeat_counter = 0
    
    while True:
        try:
            cycle_start = time.time()
            
            # Process pending PRs
            processed1 = process_pending_prs()
            processed2 = process_github_pr_mcps()
            
            total_processed = processed1 + processed2
            if total_processed > 0:
                log.info(f"Cycle complete: processed {total_processed} MCPs (pending: {processed1}, recheck: {processed2})")
            
            # Heartbeat every cycle
            heartbeat_counter += 1
            if heartbeat_counter % 3 == 0:
                send_heartbeat()
                heartbeat_counter = 0
            
            # Sleep with remainder for cycle timing
            elapsed = time.time() - cycle_start
            sleep_time = max(1, POLL_SECS - elapsed)
            time.sleep(sleep_time)
            
        except Exception as e:
            log.error(f"Error in main loop: {e}")
            time.sleep(POLL_SECS)

def main():
    """Entry point for running as daemon."""
    run()

if __name__ == "__main__":
    # Run as daemon with heartbeat thread
    import threading
    
    def heartbeat_loop():
        while True:
            try:
                send_heartbeat()
            except Exception as e:
                log.error(f"Heartbeat error: {e}")
            time.sleep(HEARTBEAT_INTERVAL)
    
    # Start heartbeat in background thread
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    # Run FastAPI server
    uvicorn.run(app, host="127.0.0.1", port=PORT)