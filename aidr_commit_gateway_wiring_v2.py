import os
import sys
import time
import signal
import logging
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

# Configuration
SERVICE_NAME = "aidr_commit_gateway_wiring_v2"
PORT = 8784
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
CYCLE_INTERVAL = 30
HEARTBEAT_INTERVAL = 30

# Verdict blocking configuration
VERDICTS_BLOCKED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "MALICIOUS", "SUSPICIOUS"]
VERDICTS_SAFE = ["TRUSTED", "VERIFIED", "SAFE", "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"]
VERDICT_ALLOWED_OVERRIDE = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
LOG = logging.getLogger(SERVICE_NAME)

start_time = time.time()
last_heartbeat = time.time()
pending_commits: List[Dict[str, Any]] = []

def check_single_instance():
    """Ensure only one instance is running."""
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            existing_pid = f.read().strip()
        if existing_pid and existing_pid != pid:
            try:
                os.kill(int(existing_pid), 0)
                LOG.error(f"Another instance already running with PID {existing_pid}")
                sys.exit(1)
            except OSError:
                LOG.warning(f"Stale PID file found, removing")
                os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(pid)
    LOG.info(f"Started with PID {pid}")

def remove_pid_file():
    """Remove PID file on shutdown."""
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    LOG.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)

def send_heartbeat():
    """Send heartbeat to service_health table via write_service."""
    global last_heartbeat
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat()
            }
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            last_heartbeat = time.time()
            return True
        else:
            LOG.warning(f"Heartbeat failed: {resp.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        LOG.error(f"Heartbeat error: {e}")
        return False

def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    """Execute SELECT query via write_service."""
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except requests.exceptions.RequestException as e:
        LOG.error(f"ws_query failed: {e}")
        return None

def ws_write(rows: Dict[str, Any]) -> bool:
    """Write to write_service via POST /write with 'rows' field."""
    try:
        payload = {
            "table": "audit_log",
            "rows": rows
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        LOG.error(f"ws_write failed: {e}")
        return False

def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        LOG.error(f"ws_execute failed: {e}")
        return False

def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    """Query trust_synthesiser for current server verdict."""
    sql = f"""
    SELECT verdict, trust_score, reasoning, computed_at
    FROM mcp_server_registry
    WHERE server_id = '{server_id}'
    ORDER BY computed_at DESC
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0]
    return None

def get_injection_resilience_score(server_id: str) -> Optional[float]:
    """Get injection_resilience signal score for server."""
    sql = f"""
    SELECT score
    FROM mcp_signal_scores
    WHERE server_id = '{server_id}' 
    AND signal_name = 'injection_resilience'
    ORDER BY scored_at DESC
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return float(rows[0].get("score", 0.0))
    return 0.0

def check_verdict_blocked(verdict: str) -> bool:
    """Check if verdict is in blocked list."""
    return verdict in VERDICTS_BLOCKED

def should_block_commit(verdict: str, force_commit: bool = False, override_reason: Optional[str] = None) -> tuple:
    """Determine if commit should be blocked based on verdict."""
    if verdict in VERDICTS_BLOCKED:
        if force_commit and override_reason:
            if verdict in VERDICT_ALLOWED_OVERRIDE:
                return False, f"Override accepted for {verdict}: {override_reason}"
            else:
                return True, f"Override not allowed for {verdict}"
        return True, f"Verdict {verdict} is blocked"
    return False, "Verdict approved"

def log_commit_decision(
    commit_id: str,
    server_id: str,
    verdict: str,
    blocked: bool,
    injection_score: float,
    force_commit: bool,
    override_reason: Optional[str],
    message: str
) -> None:
    """Log commit decision to audit_log table."""
    from uuid import uuid4
    event_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    
    details = {
        "commit_id": commit_id,
        "server_id": server_id,
        "verdict": verdict,
        "injection_resilience_score": injection_score,
        "force_commit": force_commit,
        "override_reason": override_reason,
        "blocked": blocked,
        "message": message
    }
    
    audit_row = {
        "event_id": event_id,
        "event_type": "COMMIT_DECISION",
        "actor": SERVICE_NAME,
        "target_server_id": server_id,
        "action": "GATEWAY_COMMIT_CHECK",
        "outcome": "BLOCKED" if blocked else "ALLOWED",
        "details_json": str(details),
        "timestamp": timestamp
    }
    
    ws_write(audit_row)
    LOG.info(f"Logged commit decision: {commit_id} -> {'BLOCKED' if blocked else 'ALLOWED'}")

def process_commit(server_id: str, commit_hash: str, force_commit: bool = False, override_reason: Optional[str] = None) -> Dict[str, Any]:
    """Process a commit request through the gateway."""
    commit_id = f"commit_{int(time.time() * 1000)}"
    
    # Step 1: Get current verdict from trust_synthesiser
    verdict_data = get_server_verdict(server_id)
    if not verdict_data:
        LOG.warning(f"No verdict found for server {server_id}, defaulting to UNKNOWN")
        verdict = "UNKNOWN"
        trust_score = 0.0
        reasoning = "No verdict found in trust_synthesiser"
    else:
        verdict = verdict_data.get("verdict", "UNKNOWN")
        trust_score = float(verdict_data.get("trust_score", 0.0))
        reasoning = verdict_data.get("reasoning", "")
    
    # Step 2: Get injection resilience score
    injection_score = get_injection_resilience_score(server_id)
    
    # Step 3: Check if commit should be blocked
    blocked, block_message = should_block_commit(verdict, force_commit, override_reason)
    
    # Step 4: Build commit payload with injection_resilience score
    commit_payload = {
        "commit_id": commit_id,
        "server_id": server_id,
        "commit_hash": commit_hash,
        "verdict": verdict,
        "trust_score": trust_score,
        "injection_resilience_score": injection_score,
        "blocked": blocked,
        "reasoning": reasoning,
        "message": block_message
    }
    
    # Step 5: Log decision to audit_log
    log_commit_decision(
        commit_id=commit_id,
        server_id=server_id,
        verdict=verdict,
        blocked=blocked,
        injection_score=injection_score,
        force_commit=force_commit,
        override_reason=override_reason,
        message=block_message
    )
    
    return commit_payload

def evaluate_pending_commits() -> List[Dict[str, Any]]:
    """Evaluate all pending commits in the queue."""
    results = []
    for commit in pending_commits[:]:
        result = process_commit(
            server_id=commit.get("server_id"),
            commit_hash=commit.get("commit_hash", ""),
            force_commit=commit.get("force_commit", False),
            override_reason=commit.get("override_reason")
        )
        results.append(result)
        pending_commits.remove(commit)
    return results

def add_commit_to_queue(server_id: str, commit_hash: str, force_commit: bool = False, override_reason: Optional[str] = None) -> None:
    """Add a commit to the processing queue."""
    commit = {
        "server_id": server_id,
        "commit_hash": commit_hash,
        "force_commit": force_commit,
        "override_reason": override_reason,
        "queued_at": time.time()
    }
    pending_commits.append(commit)
    LOG.info(f"Added commit to queue: server={server_id}, hash={commit_hash}")

def ensure_audit_log_table() -> None:
    """Ensure audit_log table exists with proper schema."""
    sql = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        event_id VARCHAR UNIQUE NOT NULL,
        event_type VARCHAR NOT NULL,
        actor VARCHAR NOT NULL,
        target_server_id VARCHAR,
        action VARCHAR NOT NULL,
        outcome VARCHAR NOT NULL,
        details_json TEXT,
        timestamp TIMESTAMPTZ DEFAULT now(),
        immutable BOOLEAN DEFAULT true
    )
    """
    ws_execute(sql)

def get_service_health() -> Dict[str, Any]:
    """Get current service health status."""
    uptime = int(time.time() - start_time)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime_seconds": uptime,
        "pending_commits": len(pending_commits),
        "last_heartbeat": datetime.fromtimestamp(last_heartbeat, tz=timezone.utc).isoformat()
    }

def run():
    """Main daemon loop."""
    LOG.info(f"Starting {SERVICE_NAME}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_audit_log_table()
    LOG.info("Audit log table verified")
    
    heartbeat_count = 0
    cycle_count = 0
    
    while True:
        try:
            cycle_count += 1
            
            # Send heartbeat
            send_heartbeat()
            heartbeat_count += 1
            
            # Process pending commits
            results = evaluate_pending_commits()
            if results:
                LOG.info(f"Processed {len(results)} commits in cycle {cycle_count}")
            
            # Log health periodically
            if cycle_count % 10 == 0:
                health = get_service_health()
                LOG.info(f"Health: {health}")
            
            time.sleep(CYCLE_INTERVAL)
            
        except Exception as e:
            LOG.error(f"Error in main loop: {e}")
            time.sleep(CYCLE_INTERVAL)

if __name__ == "__main__":
    run()