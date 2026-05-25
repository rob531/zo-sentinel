import os
import sys
import time
import json
import logging
import signal
import hashlib
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

SERVICE_NAME = "aidr_verdict_gateway"
SERVICE_PORT = 8792
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(SERVICE_NAME)

HEARTBEAT_INTERVAL = 60
COMMIT_ENDPOINT = "http://localhost:3891/commit"
COMMIT_TIMEOUT = 30
VERDICT_BLOCK_LIST = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"]
TRUSTED_VERDICTS = ["TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"]


def check_single_instance():
    pid = os.getpid()
    try:
        with open(PID_FILE, "r") as f:
            existing_pid = int(f.read().strip())
        if existing_pid and existing_pid != pid:
            try:
                os.kill(existing_pid, 0)
                log.error(f"Another instance running with PID {existing_pid}")
                sys.exit(1)
            except OSError:
                log.warning(f"Stale PID file found, overwriting")
    except FileNotFoundError:
        pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    log.info(f"PID {pid} registered to {PID_FILE}")


def remove_pid_file():
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass


def signal_handler(signum, frame):
    log.info(f"Signal {signum} received, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json=payload,
            timeout=15
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=15
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return False


def send_heartbeat():
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": utc_now_iso(),
            "status": "ok",
            "meta": json.dumps({"pid": os.getpid()})
        }])
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")


def compute_row_id(server_id: str, event_type: str, ts: str) -> str:
    content = f"{server_id}:{event_type}:{ts}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def log_audit(server_id: str, event_type: str, actor: str, detail: str) -> bool:
    ts = utc_now_iso()
    row_id = compute_row_id(server_id, event_type, ts)
    return ws_write("audit_log", [{
        "id": row_id,
        "target_server_id": server_id,
        "event_type": event_type,
        "actor": actor,
        "detail": detail,
        "created_at": ts
    }])


def get_server_verdict(server_id: str) -> Optional[str]:
    sql = """
    SELECT verdict FROM mcp_server_registry 
    WHERE server_id = ? 
    ORDER BY last_seen DESC LIMIT 1
    """
    rows = ws_query(sql, (server_id,))
    if rows:
        return rows[0].get("verdict")
    return None


def get_trust_score(server_id: str) -> Optional[float]:
    sql = """
    SELECT trust_score FROM mcp_server_registry 
    WHERE server_id = ? 
    ORDER BY last_seen DESC LIMIT 1
    """
    rows = ws_query(sql, (server_id,))
    if rows:
        return rows[0].get("trust_score")
    return None


def get_injection_resilience_score(server_id: str) -> Optional[float]:
    sql = """
    SELECT score FROM mcp_signal_scores 
    WHERE server_id = ? AND signal_name = 'injection_resilience' 
    ORDER BY scored_at DESC LIMIT 1
    """
    rows = ws_query(sql, (server_id,))
    if rows:
        return rows[0].get("score")
    return None


def get_signal_scores(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT signal_name, score, evidence, scored_at 
    FROM mcp_signal_scores 
    WHERE server_id = ? 
    ORDER BY scored_at DESC
    """
    return ws_query(sql, (server_id,))


def get_verdict_history(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT verdict, trust_score, last_seen 
    FROM mcp_server_registry 
    WHERE server_id = ? 
    ORDER BY last_seen DESC 
    LIMIT 10
    """
    return ws_query(sql, (server_id,))


def is_override_present(payload: Dict[str, Any]) -> bool:
    return payload.get("_override_verdict_block", False) is True


def check_verdict_allowed(verdict: Optional[str], trust_score: Optional[float], 
                          override: bool = False) -> tuple[bool, str]:
    if verdict is None:
        return False, "No verdict found for server"
    
    if verdict in VERDICT_BLOCK_LIST:
        if override:
            log.warning(f"Override present for blocked verdict: {verdict}")
            return True, f"Override accepted for {verdict}"
        return False, f"Verdict '{verdict}' is blocked from auto-commit"
    
    if verdict in TRUSTED_VERDICTS:
        return True, f"Verdict '{verdict}' is trusted"
    
    if trust_score is not None and trust_score >= 75.0:
        return True, f"Trust score {trust_score:.1f} above threshold"
    
    if trust_score is not None and trust_score >= 50.0:
        log.warning(f"Moderate trust score {trust_score:.1f} - proceeding with caution")
        return True, f"Proceeding with moderate trust score {trust_score:.1f}"
    
    return False, f"Verdict '{verdict}' with trust score {trust_score} is below minimum threshold"


def enrich_commit_payload(server_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    enriched = payload.copy()
    
    injection_score = get_injection_resilience_score(server_id)
    if injection_score is not None:
        enriched["injection_resilience_score"] = injection_score
        log.info(f"Added injection_resilience_score={injection_score:.4f} to commit payload")
    
    signals = get_signal_scores(server_id)
    if signals:
        signal_summary = {}
        for sig in signals:
            sn = sig.get("signal_name", "")
            sc = sig.get("score")
            if sn and sc is not None:
                signal_summary[sn] = round(sc, 4)
        enriched["signal_summary"] = signal_summary
    
    return enriched


def forward_commit(server_id: str, enriched_payload: Dict[str, Any]) -> tuple[bool, str]:
    try:
        headers = {"Content-Type": "application/json"}
        resp = requests.post(
            COMMIT_ENDPOINT,
            json=enriched_payload,
            headers=headers,
            timeout=COMMIT_TIMEOUT
        )
        if resp.status_code in (200, 201):
            log.info(f"Commit forwarded successfully for {server_id}")
            return True, "Commit forwarded successfully"
        else:
            msg = f"Commit endpoint returned {resp.status_code}"
            log.error(msg)
            return False, msg
    except requests.exceptions.Timeout:
        msg = "Commit endpoint timeout"
        log.error(msg)
        return False, msg
    except Exception as e:
        msg = f"Commit forward failed: {e}"
        log.error(msg)
        return False, msg


def process_commit(server_id: str, commit_payload: Dict[str, Any]) -> Dict[str, Any]:
    log.info(f"Processing commit request for server: {server_id}")
    
    verdict = get_server_verdict(server_id)
    trust_score = get_trust_score(server_id)
    
    log.info(f"Server {server_id}: verdict={verdict}, trust_score={trust_score}")
    
    override = is_override_present(commit_payload)
    
    allowed, reason = check_verdict_allowed(verdict, trust_score, override)
    
    if not allowed:
        log.warning(f"Commit BLOCKED for {server_id}: {reason}")
        log_audit(server_id, "commit_blocked", "aidr_verdict_gateway", reason)
        return {
            "allowed": False,
            "blocked": True,
            "reason": reason,
            "verdict": verdict,
            "trust_score": trust_score,
            "server_id": server_id
        }
    
    enriched = enrich_commit_payload(server_id, commit_payload)
    
    if "_override_verdict_block" in enriched:
        del enriched["_override_verdict_block"]
    
    success, msg = forward_commit(server_id, enriched)
    
    if success:
        log_audit(server_id, "commit_forwarded", "aidr_verdict_gateway", 
                  f"Allowed: verdict={verdict}, trust_score={trust_score}")
        log.info(f"Commit ALLOWED for {server_id}: {reason}")
    else:
        log_audit(server_id, "commit_forwarded_failed", "aidr_verdict_gateway", msg)
    
    return {
        "allowed": True,
        "blocked": False,
        "reason": reason,
        "verdict": verdict,
        "trust_score": trust_score,
        "server_id": server_id,
        "commit_success": success,
        "commit_message": msg
    }


def get_pending_decisions() -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, submission_id, verdict, trust_score, created_at 
    FROM approval_submissions 
    WHERE status = 'pending_verdict_check' 
    AND created_at > (NOW() - INTERVAL '1 hour')
    LIMIT 100
    """
    return ws_query(sql)


def process_pending_decisions() -> int:
    pending = get_pending_decisions()
    if not pending:
        return 0
    
    log.info(f"Found {len(pending)} pending decisions")
    processed = 0
    
    for item in pending:
        server_id = item.get("server_id")
        if not server_id:
            continue
        
        result = process_commit(server_id, {"server_id": server_id})
        if result.get("allowed"):
            processed += 1
    
    return processed


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    send_heartbeat()
    
    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            processed = process_pending_decisions()
            if processed > 0:
                log.info(f"Processed {processed} decisions this cycle")
            
            if cycle_count % 10 == 0:
                log.info(f"Cycle {cycle_count}: {processed} decisions processed")
            
            send_heartbeat()
            
        except Exception as e:
            log.error(f"Error in main loop: {e}")
        
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    run()