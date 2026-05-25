import os
import sys
import time
import json
import signal
import hashlib
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

SERVICE_NAME = "aidr_verdict_enforcer"
SERVICE_PORT = 8788
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
HEARTBEAT_INTERVAL = 30
POLL_SECS = 5

BLOCKED_VERDICTS = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"]
CAUTION_LIMITED = "CAUTION_LIMITED"
HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
KNOWN_THREAT = "KNOWN_THREAT"

AIDR_COMMIT_GATEWAY_URL = "http://127.0.0.1:8783"
COMMIT_PAYLOAD_QUEUE = "/tmp/zo_sentinel/commit_payload_queue.json"
COMMIT_DECISION_LOG = "/tmp/zo_sentinel/commit_decisions.log"

os.makedirs("/tmp/zo_sentinel", exist_ok=True)

def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        if os.path.exists(f"/proc/{old_pid}"):
            print(f"[{SERVICE_NAME}] Already running with PID {old_pid}")
            sys.exit(0)
        else:
            print(f"[{SERVICE_NAME}] Stale PID file found, removing")
            remove_pid_file()
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def signal_handler(signum, frame):
    print(f"[{SERVICE_NAME}] Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[{SERVICE_NAME}] Query error: {e}")
        return {"rows": [], "count": 0}

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[{SERVICE_NAME}] Write error: {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[{SERVICE_NAME}] Execute error: {e}")
        return False

def send_heartbeat():
    try:
        payload = {
            "table": "service_health",
            "rows": [{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}],
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"[{SERVICE_NAME}] Heartbeat error: {e}")

def log_commit_decision(server_id: str, decision: str, reason: str, verdict: str = None, injection_score: float = None):
    timestamp = datetime.utcnow().isoformat()
    log_entry = {
        "timestamp": timestamp,
        "service": SERVICE_NAME,
        "target_server_id": server_id,
        "decision": decision,
        "reason": reason,
        "verdict": verdict,
        "injection_resilience_score": injection_score
    }
    
    try:
        with open(COMMIT_DECISION_LOG, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to write decision log: {e}")
    
    try:
        audit_row = {
            "target_server_id": server_id,
            "event_type": f"COMMIT_{decision.upper()}",
            "actor": SERVICE_NAME,
            "detail": json.dumps({"reason": reason, "verdict": verdict, "injection_resilience_score": injection_score}),
            "created_at": timestamp
        }
        ws_write("audit_log", [audit_row])
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to write audit log: {e}")

def get_server_verdict(server_id: str) -> Optional[str]:
    result = ws_query(f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0].get("verdict")
    return None

def get_injection_resilience_score(server_id: str) -> Optional[float]:
    result = ws_query(f"""
        SELECT score FROM mcp_signal_scores 
        WHERE server_id = '{server_id}' 
        AND signal_name = 'injection_resilience'
        ORDER BY scored_at DESC LIMIT 1
    """)
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0].get("score")
    return None

def check_exemption_override(server_id: str) -> bool:
    result = ws_query(f"""
        SELECT id, exemption_type, expires_at FROM mcp_exemptions 
        WHERE server_id = '{server_id}' 
        AND (exemption_type = 'COMMIT_OVERRIDE' OR exemption_type = 'VERDICT_OVERRIDE')
        AND (expires_at IS NULL OR expires_at > NOW())
    """)
    if result.get("rows") and len(result["rows"]) > 0:
        for row in result["rows"]:
            if row.get("exemption_type") in ["COMMIT_OVERRIDE", "VERDICT_OVERRIDE"]:
                return True
    return False

def check_commit_allowed(server_id: str) -> Dict[str, Any]:
    verdict = get_server_verdict(server_id)
    injection_score = get_injection_resilience_score(server_id)
    
    if verdict is None:
        return {
            "allowed": False,
            "reason": "Server not found in registry",
            "verdict": None,
            "injection_score": injection_score
        }
    
    if verdict == KNOWN_THREAT:
        return {
            "allowed": False,
            "reason": "KNOWN_THREAT servers are never auto-committed",
            "verdict": verdict,
            "injection_score": injection_score
        }
    
    if verdict in BLOCKED_VERDICTS:
        has_override = check_exemption_override(server_id)
        if not has_override:
            return {
                "allowed": False,
                "reason": f"Verdict '{verdict}' is blocked. Requires explicit COMMIT_OVERRIDE or VERDICT_OVERRIDE in mcp_exemptions",
                "verdict": verdict,
                "injection_score": injection_score
            }
        else:
            return {
                "allowed": True,
                "reason": f"Verdict '{verdict}' blocked but exemption override exists",
                "verdict": verdict,
                "injection_score": injection_score,
                "exemption_override": True
            }
    
    return {
        "allowed": True,
        "reason": f"Verdict '{verdict}' is approved for commit",
        "verdict": verdict,
        "injection_score": injection_score
    }

def fetch_pending_commit_payloads() -> List[Dict[str, Any]]:
    payloads = []
    try:
        if os.path.exists(COMMIT_PAYLOAD_QUEUE):
            with open(COMMIT_PAYLOAD_QUEUE, 'r') as f:
                payloads = json.load(f)
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to read commit payload queue: {e}")
    return payloads

def save_pending_commit_payloads(payloads: List[Dict[str, Any]]):
    try:
        with open(COMMIT_PAYLOAD_QUEUE, 'w') as f:
            json.dump(payloads, f)
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to save commit payload queue: {e}")

def forward_to_aidr_commit_gateway(server_id: str, original_payload: Dict[str, Any], injection_score: float):
    enriched_payload = original_payload.copy()
    enriched_payload["injection_resilience_score"] = injection_score
    enriched_payload["verdict_check_passed"] = True
    enriched_payload["verdict_check_timestamp"] = datetime.utcnow().isoformat()
    
    try:
        resp = requests.post(
            f"{AIDR_COMMIT_GATEWAY_URL}/commit",
            json=enriched_payload,
            timeout=60
        )
        resp.raise_for_status()
        return {"success": True, "response": resp.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}

def ensure_audit_table():
    sql = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY,
        target_server_id VARCHAR,
        event_type VARCHAR,
        actor VARCHAR,
        detail VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(sql)

def process_commit_queue():
    payloads = fetch_pending_commit_payloads()
    if not payloads:
        return
    
    processed = []
    for payload in payloads:
        server_id = payload.get("server_id") or payload.get("server_id") or payload.get("mcp_server_id")
        
        if not server_id:
            processed.append(payload)
            continue
        
        check_result = check_commit_allowed(server_id)
        
        if check_result["allowed"]:
            log_commit_decision(
                server_id=server_id,
                decision="APPROVED",
                reason=check_result["reason"],
                verdict=check_result["verdict"],
                injection_score=check_result["injection_score"]
            )
            
            injection_score = check_result["injection_score"] or 0.0
            forward_result = forward_to_aidr_commit_gateway(
                server_id=server_id,
                original_payload=payload,
                injection_score=injection_score
            )
            
            if forward_result.get("success"):
                print(f"[{SERVICE_NAME}] Committed server {server_id} with injection_score={injection_score}")
            else:
                print(f"[{SERVICE_NAME}] Failed to forward to commit gateway: {forward_result.get('error')}")
                payload["forward_error"] = forward_result.get("error")
                processed.append(payload)
        else:
            log_commit_decision(
                server_id=server_id,
                decision="BLOCKED",
                reason=check_result["reason"],
                verdict=check_result["verdict"],
                injection_score=check_result["injection_score"]
            )
            print(f"[{SERVICE_NAME}] BLOCKED commit for server {server_id}: {check_result['reason']}")
            processed.append(payload)
    
    save_pending_commit_payloads(processed)

def query_commit_decisions(days: int = 7) -> List[Dict[str, Any]]:
    result = ws_query(f"""
        SELECT target_server_id, event_type, actor, detail, created_at
        FROM audit_log
        WHERE event_type LIKE 'COMMIT_%'
        AND created_at >= NOW() - INTERVAL '{days} days'
        ORDER BY created_at DESC
    """)
    return result.get("rows", [])

def get_commit_statistics() -> Dict[str, Any]:
    approved = ws_query("""
        SELECT COUNT(*) as count FROM audit_log 
        WHERE event_type = 'COMMIT_APPROVED'
        AND created_at >= NOW() - INTERVAL '7 days'
    """)
    
    blocked = ws_query("""
        SELECT COUNT(*) as count FROM audit_log 
        WHERE event_type = 'COMMIT_BLOCKED'
        AND created_at >= NOW() - INTERVAL '7 days'
    """)
    
    return {
        "approved_7d": approved.get("rows", [{}])[0].get("count", 0) if approved.get("rows") else 0,
        "blocked_7d": blocked.get("rows", [{}])[0].get("count", 0) if blocked.get("rows") else 0
    }

def cycle():
    ensure_audit_table()
    
    process_commit_queue()
    
    stats = get_commit_statistics()
    if stats["approved_7d"] > 0 or stats["blocked_7d"] > 0:
        print(f"[{SERVICE_NAME}] Stats: {stats['approved_7d']} approved, {stats['blocked_7d']} blocked (7d)")

def run():
    print(f"[{SERVICE_NAME}] Starting AiDr Verdict Enforcer...")
    
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_audit_table()
    
    start_time = time.time()
    last_heartbeat = start_time
    
    while True:
        try:
            cycle()
            
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = now
            
            time.sleep(POLL_SECS)
        except Exception as e:
            print(f"[{SERVICE_NAME}] Error in main loop: {e}")
            time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()