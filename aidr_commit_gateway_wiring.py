import sys
import os
import json
import time
import signal
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = "aidr_commit_gateway_wiring"
SERVICE_PORT = 8786
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = "/home/workspace/zo_sentinel/logs/aidr_commit_gateway_wiring.log"
LOG_DIR = "/home/workspace/zo_sentinel/logs"

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"

HEARTBEAT_INTERVAL = 60
AIDR_ENDPOINT = os.environ.get("AIDR_ENDPOINT", "http://127.0.0.1:8787")
AIDR_API_KEY = os.environ.get("AIDR_API_KEY", "")

VERDICT_RISK_THRESHOLD = "CAUTION_LIMITED"
VERDICT_BLOCKED = "HIGH_RISK_ISOLATED"
PRIORITY_SCORE = 0.90


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(SERVICE_NAME)


logger = setup_logging()


def check_single_instance():
    """Ensure only one instance runs."""
    pid = os.getpid()
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                existing_pid = int(f.read().strip())
            try:
                os.kill(existing_pid, 0)
                logger.error(f"Another instance running: {existing_pid}")
                sys.exit(1)
            except OSError:
                logger.info(f"Stale PID file found, removing")
        with open(PID_FILE, 'w') as f:
            f.write(str(pid))
        logger.info(f"Started with PID {pid}")
    except Exception as e:
        logger.error(f"Error checking instance: {e}")
        sys.exit(1)


def remove_pid_file():
    """Remove PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
            logger.info("PID file removed")
    except Exception as e:
        logger.error(f"Error removing PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write to write_service with 'rows' not 'row'."""
    try:
        import requests
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write error: {e}")
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    """Query write_service."""
    try:
        import requests
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query error: {e}")
        return None


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        import requests
        resp = requests.post(
            f"{EXECUTE_SERVICE_URL}/execute",
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute error: {e}")
        return False


def send_heartbeat():
    """Send heartbeat to service_health."""
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        }])
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")


def ensure_tables():
    """Ensure required tables exist."""
    tables = [
        """CREATE TABLE IF NOT EXISTS aidr_commit_log (
            commit_id TEXT PRIMARY KEY,
            server_id TEXT,
            mcp_name TEXT,
            verdict TEXT,
            risk_tier TEXT,
            injection_resilience_score REAL,
            commit_decision TEXT,
            decision_reason TEXT,
            override_flag BOOLEAN DEFAULT FALSE,
            signal_summary TEXT,
            committed_at TEXT,
            aidr_response TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS aidr_commit_queue (
            queue_id TEXT PRIMARY KEY,
            server_id TEXT,
            mcp_name TEXT,
            commit_request TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            processed_at TEXT
        )"""
    ]
    for sql in tables:
        ws_execute(sql)


def get_trust_synthesiser_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    """Query trust_synthesiser_v2 for server verdict."""
    try:
        result = ws_query(f"""
            SELECT server_id, mcp_name, trust_score, verdict, risk_tier,
                   injection_resilience_score, signal_summary
            FROM mcp_server_registry
            WHERE server_id = '{server_id}'
        """)
        if result and len(result) > 0:
            return result[0]
        
        result = ws_query(f"""
            SELECT server_id, mcp_name, trust_score, verdict, risk_tier
            FROM mcp_server_registry
            WHERE mcp_name = '{server_id}'
            LIMIT 1
        """)
        if result and len(result) > 0:
            return result[0]
        
        return None
    except Exception as e:
        logger.error(f"Error querying trust_synthesiser_v2: {e}")
        return None


def get_injection_resilience_score(server_id: str, mcp_name: str) -> float:
    """Compute injection_resilience score via pi_scorer or query existing."""
    try:
        result = ws_query(f"""
            SELECT score
            FROM mcp_signal_scores
            WHERE server_id = '{server_id}'
            AND signal_name = 'injection_resilience'
            ORDER BY scored_at DESC
            LIMIT 1
        """)
        if result and len(result) > 0:
            return float(result[0].get('score', 0.0))
        
        result = ws_query(f"""
            SELECT score
            FROM mcp_signal_scores
            WHERE server_id IN (
                SELECT server_id FROM mcp_server_registry WHERE mcp_name = '{mcp_name}'
            )
            AND signal_name = 'injection_resilience'
            ORDER BY scored_at DESC
            LIMIT 1
        """)
        if result and len(result) > 0:
            return float(result[0].get('score', 0.0))
        
        return 0.5
    except Exception as e:
        logger.error(f"Error getting injection_resilience score: {e}")
        return 0.5


def get_full_signal_summary(server_id: str) -> Dict[str, Any]:
    """Get full signal summary for commit payload."""
    try:
        signals = ws_query(f"""
            SELECT signal_name, score, evidence
            FROM mcp_signal_scores
            WHERE server_id = '{server_id}'
            ORDER BY scored_at DESC
        """)
        
        signal_map = {}
        if signals:
            for sig in signals:
                name = sig.get('signal_name', '')
                if name not in signal_map:
                    signal_map[name] = {
                        'score': sig.get('score', 0.0),
                        'evidence': sig.get('evidence', '')
                    }
        
        return signal_map
    except Exception as e:
        logger.error(f"Error getting signal summary: {e}")
        return {}


def evaluate_commit_guardrails(
    verdict: str,
    risk_tier: str,
    injection_score: float,
    override_flag: bool = False
) -> tuple[bool, str]:
    """Evaluate commit guardrails per Phase 9 directive.
    
    NEVER auto-commit CAUTION_LIMITED or HIGH_RISK_ISOLATED without explicit override flag.
    """
    blocked_verdicts = [VERDICT_RISK_THRESHOLD, VERDICT_BLOCKED]
    
    if risk_tier in blocked_verdicts and not override_flag:
        return False, f"BLOCKED: risk_tier={risk_tier} requires explicit override_flag"
    
    if injection_score < 0.3:
        return False, f"BLOCKED: injection_resilience_score={injection_score} below threshold (0.3)"
    
    if verdict in ['MALICIOUS', 'THREAT_CONFIRMED']:
        return False, f"BLOCKED: verdict={verdict} cannot be committed"
    
    return True, "APPROVED: all guardrails passed"


def commit_to_aidr(
    server_id: str,
    mcp_name: str,
    verdict: str,
    risk_tier: str,
    injection_score: float,
    signal_summary: Dict[str, Any],
    override_flag: bool = False
) -> tuple[bool, str, Optional[Dict[str, Any]]]:
    """Send commit request to AiDr with full signal payload."""
    commit_id = f"commit_{int(time.time())}_{server_id[:8]}"
    
    commit_payload = {
        "commit_id": commit_id,
        "server_id": server_id,
        "mcp_name": mcp_name,
        "verdict": verdict,
        "risk_tier": risk_tier,
        "injection_resilience_score": injection_score,
        "priority_score": PRIORITY_SCORE,
        "signal_summary": signal_summary,
        "override_flag": override_flag,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        import requests
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": AIDR_API_KEY
        }
        
        resp = requests.post(
            f"{AIDR_ENDPOINT}/commit",
            json=commit_payload,
            headers=headers,
            timeout=60
        )
        
        if resp.status_code in [200, 201, 202]:
            logger.info(f"Commit successful: {commit_id}")
            return True, "Committed to AiDr", resp.json() if resp.text else None
        else:
            logger.warning(f"Commit returned status {resp.status_code}: {resp.text}")
            return False, f"AiDr rejected commit: status={resp.status_code}", None
            
    except requests.exceptions.ConnectionError:
        logger.warning(f"AiDr endpoint not reachable: {AIDR_ENDPOINT}")
        return False, f"AiDr endpoint unreachable: {AIDR_ENDPOINT}", None
    except Exception as e:
        logger.error(f"Commit error: {e}")
        return False, str(e), None


def write_commit_audit(
    commit_id: str,
    server_id: str,
    mcp_name: str,
    verdict: str,
    risk_tier: str,
    injection_score: float,
    decision: str,
    reason: str,
    override_flag: bool,
    signal_summary: Dict[str, Any],
    aidr_response: Optional[Dict[str, Any]]
):
    """Write audit log entry for every commit decision."""
    ws_write("audit_log", [{
        "target_server_id": server_id,
        "event_type": "AIDR_COMMIT_DECISION",
        "actor": SERVICE_NAME,
        "detail": json.dumps({
            "commit_id": commit_id,
            "mcp_name": mcp_name,
            "verdict": verdict,
            "risk_tier": risk_tier,
            "injection_resilience_score": injection_score,
            "decision": decision,
            "reason": reason,
            "override_flag": override_flag,
            "signal_summary_keys": list(signal_summary.keys()),
            "aidr_response_success": aidr_response is not None
        }),
        "created_at": datetime.utcnow().isoformat()
    }])
    
    ws_write("aidr_commit_log", [{
        "commit_id": commit_id,
        "server_id": server_id,
        "mcp_name": mcp_name,
        "verdict": verdict,
        "risk_tier": risk_tier,
        "injection_resilience_score": injection_score,
        "commit_decision": decision,
        "decision_reason": reason,
        "override_flag": override_flag,
        "signal_summary": json.dumps(signal_summary),
        "committed_at": datetime.utcnow().isoformat(),
        "aidr_response": json.dumps(aidr_response) if aidr_response else None
    }])


def process_commit_request(
    server_id: str,
    mcp_name: Optional[str] = None,
    override_flag: bool = False
) -> Dict[str, Any]:
    """Process a commit request end-to-end."""
    logger.info(f"Processing commit request: server_id={server_id}, override={override_flag}")
    
    server_info = get_trust_synthesiser_verdict(server_id)
    if not server_info:
        return {
            "success": False,
            "error": f"Server not found: {server_id}",
            "commit_id": None
        }
    
    actual_server_id = server_info.get('server_id', server_id)
    actual_mcp_name = server_info.get('mcp_name', mcp_name or server_id)
    verdict = server_info.get('verdict', 'UNKNOWN')
    risk_tier = server_info.get('risk_tier', 'UNKNOWN')
    
    injection_score = get_injection_resilience_score(actual_server_id, actual_mcp_name)
    signal_summary = get_full_signal_summary(actual_server_id)
    
    approved, reason = evaluate_commit_guardrails(
        verdict, risk_tier, injection_score, override_flag
    )
    
    commit_id = f"commit_{int(time.time())}_{actual_server_id[:8]}"
    
    if approved:
        success, commit_reason, aidr_resp = commit_to_aidr(
            actual_server_id,
            actual_mcp_name,
            verdict,
            risk_tier,
            injection_score,
            signal_summary,
            override_flag
        )
        
        write_commit_audit(
            commit_id,
            actual_server_id,
            actual_mcp_name,
            verdict,
            risk_tier,
            injection_score,
            "APPROVED" if success else "COMMIT_FAILED",
            f"{reason} | {commit_reason}",
            override_flag,
            signal_summary,
            aidr_resp
        )
        
        return {
            "success": success,
            "commit_id": commit_id if success else None,
            "verdict": verdict,
            "risk_tier": risk_tier,
            "injection_resilience_score": injection_score,
            "decision": "APPROVED" if success else "COMMIT_FAILED",
            "reason": f"{reason} | {commit_reason}",
            "aidr_response": aidr_resp
        }
    else:
        write_commit_audit(
            commit_id,
            actual_server_id,
            actual_mcp_name,
            verdict,
            risk_tier,
            injection_score,
            "BLOCKED",
            reason,
            override_flag,
            signal_summary,
            None
        )
        
        return {
            "success": False,
            "commit_id": None,
            "verdict": verdict,
            "risk_tier": risk_tier,
            "injection_resilience_score": injection_score,
            "decision": "BLOCKED",
            "reason": reason,
            "aidr_response": None
        }


from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI()

class CommitRequest(BaseModel):
    server_id: str
    mcp_name: Optional[str] = None
    override_flag: bool = False


class CommitResponse(BaseModel):
    success: bool
    commit_id: Optional[str]
    verdict: str
    risk_tier: str
    injection_resilience_score: float
    decision: str
    reason: str
    aidr_response: Optional[Dict[str, Any]]


@app.post("/commit")
def handle_commit(
    request: CommitRequest,
    x_api_key: Optional[str] = Header(None)
):
    """Accept commit request via POST."""
    if AIDR_API_KEY and x_api_key != AIDR_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    return process_commit_request(
        request.server_id,
        request.mcp_name,
        request.override_flag
    )


@app.get("/health")
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/commit/{commit_id}")
def get_commit_status(commit_id: str):
    """Get status of a commit."""
    result = ws_query(f"""
        SELECT * FROM aidr_commit_log
        WHERE commit_id = '{commit_id}'
    """)
    if result and len(result) > 0:
        return result[0]
    raise HTTPException(status_code=404, detail="Commit not found")


@app.get("/commits/recent")
def get_recent_commits(limit: int = 50):
    """Get recent commit log entries."""
    result = ws_query(f"""
        SELECT commit_id, server_id, mcp_name, verdict, risk_tier,
               injection_resilience_score, commit_decision, committed_at
        FROM aidr_commit_log
        ORDER BY committed_at DESC
        LIMIT {limit}
    """)
    return {"commits": result or []}


def heartbeat_loop():
    """Send heartbeat every HEARTBEAT_INTERVAL seconds."""
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


import threading

def run():
    """Main entry point for the daemon."""
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    logger.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=SERVICE_PORT)


if __name__ == '__main__':
    run()