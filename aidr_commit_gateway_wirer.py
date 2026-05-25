import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
import os
os.chdir('/home/workspace/zo_sentinel')

import requests
import time
import logging
import signal
from datetime import datetime
from typing import Optional, Dict, Any, List

SERVICE_NAME = "aidr_commit_gateway_wirer"
PORT = 8784
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
HEARTBEAT_INTERVAL = 30
POLL_SECS = 15

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
LOG = logging.getLogger(SERVICE_NAME)

VERDICTS_BLOCKED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "MALICIOUS", "SUSPICIOUS", "KNOWN_THREAT"]
VERDICTS_SAFE = ["TRUSTED", "VERIFIED", "SAFE", "RECOMMENDED", "ENTERPRISE_CONTROLLED", "TRUSTED_RESEARCH"]
VERDICT_UNKNOWN = "UNKNOWN"

BLOCKED_VERDICT_THRESHOLDS = {
    "CAUTION_LIMITED": 0.4,
    "HIGH_RISK_ISOLATED": 0.3,
    "MALICIOUS": 0.2,
    "SUSPICIOUS": 0.35,
    "KNOWN_THREAT": 0.1
}

start_time = time.time()
running = True


def log(msg: str, level: str = "INFO"):
    getattr(LOG, level.lower())(msg)


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log(f"ws_query error: {e}", "ERROR")
        return None


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_write error: {e}", "ERROR")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log(f"ws_execute error: {e}", "ERROR")
        return False


def check_single_instance():
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Instance already running with PID {old_pid}", "ERROR")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    import os
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    global running
    log(f"Received signal {signum}, shutting down gracefully")
    running = False
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    ts = datetime.utcnow().isoformat()
    ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": ts
    })


def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT server_id, name, verdict, trust_score, risk_tier
    FROM mcp_server_registry
    WHERE server_id = '{server_id}'
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return rows[0]
    return None


def get_injection_resilience_score(server_id: str) -> Optional[float]:
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
    return None


def get_all_signal_scores(server_id: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = '{server_id}'
    ORDER BY scored_at DESC
    """
    rows = ws_query(sql) or []
    return rows


def get_trust_score(server_id: str) -> Optional[float]:
    sql = f"""
    SELECT trust_score
    FROM mcp_server_registry
    WHERE server_id = '{server_id}'
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows and len(rows) > 0:
        return float(rows[0].get("trust_score", 0.0))
    return None


def check_commit_allowed(server_id: str, force_commit: bool = False, override_reason: Optional[str] = None) -> Dict[str, Any]:
    result = {
        "allowed": False,
        "verdict": VERDICT_UNKNOWN,
        "trust_score": None,
        "injection_resilience_score": None,
        "blocked": True,
        "reason": "",
        "requires_override": False,
        "signal_scores": []
    }
    
    server_data = get_server_verdict(server_id)
    
    if not server_data:
        result["reason"] = f"Server {server_id} not found in registry"
        result["blocked"] = True
        result["requires_override"] = True
        return result
    
    verdict = server_data.get("verdict", VERDICT_UNKNOWN)
    trust_score = server_data.get("trust_score")
    
    result["verdict"] = verdict
    result["trust_score"] = trust_score
    
    signal_scores = get_all_signal_scores(server_id)
    result["signal_scores"] = signal_scores
    
    injection_score = get_injection_resilience_score(server_id)
    result["injection_resilience_score"] = injection_score
    
    if verdict in VERDICTS_BLOCKED:
        if force_commit:
            if not override_reason:
                result["reason"] = f"Override reason required for verdict {verdict}"
                result["blocked"] = True
                result["requires_override"] = True
                return result
            
            result["reason"] = f"COMMIT ALLOWED WITH OVERRIDE: {verdict} - {override_reason}"
            result["blocked"] = False
            result["requires_override"] = False
            result["override_reason"] = override_reason
            
            ws_write("audit_log", {
                "event_type": "COMMIT_OVERRIDE_ALLOWED",
                "actor": "aidr_commit_gateway_wirer",
                "target_server_id": server_id,
                "detail": f"Verdict {verdict} overridden. Reason: {override_reason}",
                "created_at": datetime.utcnow().isoformat()
            })
            
            return result
        else:
            threshold = BLOCKED_VERDICT_THRESHOLDS.get(verdict, 0.5)
            result["reason"] = f"COMMIT BLOCKED: verdict={verdict} (threshold={threshold})"
            result["blocked"] = True
            result["requires_override"] = True
            
            ws_write("audit_log", {
                "event_type": "COMMIT_BLOCKED",
                "actor": "aidr_commit_gateway_wirer",
                "target_server_id": server_id,
                "detail": f"Commit blocked for verdict {verdict}. Trust score: {trust_score}",
                "created_at": datetime.utcnow().isoformat()
            })
            
            return result
    
    if verdict in VERDICTS_SAFE:
        result["reason"] = f"COMMIT ALLOWED: verdict={verdict}"
        result["blocked"] = False
        result["requires_override"] = False
        
        if injection_score is not None and injection_score < 0.75:
            result["reason"] += f" (WARNING: low injection resilience={injection_score:.2f})"
            
            ws_write("audit_log", {
                "event_type": "COMMIT_WARNING",
                "actor": "aidr_commit_gateway_wirer",
                "target_server_id": server_id,
                "detail": f"Low injection resilience score: {injection_score:.2f}",
                "created_at": datetime.utcnow().isoformat()
            })
        
        return result
    
    if verdict == VERDICT_UNKNOWN:
        if force_commit and override_reason:
            result["reason"] = f"COMMIT ALLOWED WITH OVERRIDE: Unknown verdict - {override_reason}"
            result["blocked"] = False
            result["requires_override"] = False
            result["override_reason"] = override_reason
            return result
        
        result["reason"] = f"COMMIT REQUIRES REVIEW: verdict={verdict} (unknown)"
        result["blocked"] = True
        result["requires_override"] = True
        return result
    
    threshold = BLOCKED_VERDICT_THRESHOLDS.get(verdict, 0.5)
    if trust_score is not None and trust_score < threshold:
        if force_commit and override_reason:
            result["reason"] = f"COMMIT ALLOWED WITH OVERRIDE: Low trust score {trust_score} < {threshold} - {override_reason}"
            result["blocked"] = False
            result["requires_override"] = False
            return result
        
        result["reason"] = f"COMMIT BLOCKED: trust_score={trust_score} < threshold={threshold}"
        result["blocked"] = True
        result["requires_override"] = True
        return result
    
    result["reason"] = f"COMMIT ALLOWED: trust_score={trust_score}"
    result["blocked"] = False
    result["requires_override"] = False
    return result


def build_commit_payload(server_id: str, commit_data: Dict[str, Any]) -> Dict[str, Any]:
    verdict_result = check_commit_allowed(
        server_id=server_id,
        force_commit=commit_data.get("force_commit", False),
        override_reason=commit_data.get("override_reason")
    )
    
    payload = {
        "server_id": server_id,
        "commit_hash": commit_data.get("commit_hash"),
        "repository": commit_data.get("repository"),
        "branch": commit_data.get("branch"),
        "author": commit_data.get("author"),
        "message": commit_data.get("message"),
        "files_changed": commit_data.get("files_changed", []),
        "verdict": verdict_result["verdict"],
        "trust_score": verdict_result["trust_score"],
        "injection_resilience_score": verdict_result["injection_resilience_score"],
        "blocked": verdict_result["blocked"],
        "requires_override": verdict_result["requires_override"],
        "reason": verdict_result["reason"],
        "approved_at": datetime.utcnow().isoformat()
    }
    
    if verdict_result.get("override_reason"):
        payload["override_reason"] = verdict_result["override_reason"]
    
    return payload


def ensure_audit_table():
    sql = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY Key,
        event_type VARCHAR,
        target_server_id VARCHAR,
        actor VARCHAR,
        detail VARCHAR,
        created_at VARCHAR
    )
    """
    ws_execute(sql)


def ensure_commit_decisions_table():
    sql = """
    CREATE TABLE IF NOT EXISTS commit_decisions (
        decision_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        commit_hash VARCHAR,
        verdict VARCHAR,
        trust_score DOUBLE,
        injection_resilience_score DOUBLE,
        blocked BOOLEAN,
        requires_override BOOLEAN,
        override_reason VARCHAR,
        reason VARCHAR,
        decision_at VARCHAR,
        actor VARCHAR
    )
    """
    ws_execute(sql)


def record_commit_decision(server_id: str, commit_hash: str, verdict_result: Dict[str, Any]):
    decision_id = f"{server_id}_{commit_hash}_{int(time.time())}"
    
    ws_write("commit_decisions", {
        "decision_id": decision_id,
        "server_id": server_id,
        "commit_hash": commit_hash,
        "verdict": verdict_result.get("verdict", VERDICT_UNKNOWN),
        "trust_score": verdict_result.get("trust_score"),
        "injection_resilience_score": verdict_result.get("injection_resilience_score"),
        "blocked": verdict_result.get("blocked", True),
        "requires_override": verdict_result.get("requires_override", False),
        "override_reason": verdict_result.get("override_reason"),
        "reason": verdict_result.get("reason", ""),
        "decision_at": datetime.utcnow().isoformat(),
        "actor": SERVICE_NAME
    })


def heartbeat_loop():
    while running:
        try:
            send_heartbeat()
        except Exception as e:
            log(f"Heartbeat error: {e}", "ERROR")
        time.sleep(HEARTBEAT_INTERVAL)


def cycle():
    log("Running verdict check enforcement cycle")
    
    ensure_audit_table()
    ensure_commit_decisions_table()
    
    sql = """
    SELECT server_id, name, verdict, trust_score
    FROM mcp_server_registry
    WHERE verdict IN ('CAUTION_LIMITED', 'HIGH_RISK_ISOLATED', 'MALICIOUS', 'SUSPICIOUS', 'KNOWN_THREAT')
    LIMIT 100
    """
    blocked_servers = ws_query(sql) or []
    
    if blocked_servers:
        log(f"Found {len(blocked_servers)} servers with blocked verdicts")
        for server in blocked_servers:
            server_id = server.get("server_id")
            verdict = server.get("verdict")
            trust_score = server.get("trust_score")
            
            injection_score = get_injection_resilience_score(server_id)
            
            log(f"Server {server_id}: verdict={verdict}, trust={trust_score}, injection_resilience={injection_score}")
            
            if injection_score is not None and injection_score < 0.75:
                ws_write("audit_log", {
                    "event_type": "LOW_INJECTION_RESILIENCE_ALERT",
                    "actor": SERVICE_NAME,
                    "target_server_id": server_id,
                    "detail": f"Blocked verdict {verdict} has low injection resilience score: {injection_score:.2f}",
                    "created_at": datetime.utcnow().isoformat()
                })
    
    sql = """
    SELECT server_id, verdict, trust_score
    FROM mcp_server_registry
    WHERE verdict IN ('TRUSTED', 'VERIFIED', 'SAFE', 'RECOMMENDED')
    AND trust_score > 80
    LIMIT 50
    """
    safe_servers = ws_query(sql) or []
    
    if safe_servers:
        log(f"Verifying {len(safe_servers)} high-trust servers")
        for server in safe_servers:
            server_id = server.get("server_id")
            injection_score = get_injection_resilience_score(server_id)
            
            if injection_score is None:
                ws_write("audit_log", {
                    "event_type": "MISSING_INJECTION_RESILIENCE",
                    "actor": SERVICE_NAME,
                    "target_server_id": server_id,
                    "detail": f"High-trust server missing injection_resilience signal score",
                    "created_at": datetime.utcnow().isoformat()
                })


def run():
    global running
    
    log(f"Starting {SERVICE_NAME}")
    
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_audit_table()
    ensure_commit_decisions_table()
    
    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    log(f"{SERVICE_NAME} initialized successfully")
    log(f"Port: {PORT}")
    log(f"Write Service: {WRITE_SERVICE_URL}")
    log(f"Query Service: {QUERY_SERVICE_URL}")
    log(f"Heartbeat interval: {HEARTBEAT_INTERVAL}s")
    
    last_cycle = 0
    
    while running:
        try:
            now = time.time()
            if now - last_cycle >= POLL_SECS:
                cycle()
                last_cycle = now
        except Exception as e:
            log(f"Cycle error: {e}", "ERROR")
        
        time.sleep(5)
    
    remove_pid_file()
    log(f"{SERVICE_NAME} shutdown complete")


if __name__ == "__main__":
    run()