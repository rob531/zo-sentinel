from fastapi import FastAPI, HTTPException, Header, Body
import uvicorn
import requests
import json
import time
from typing import Optional, Dict, Any, List

SERVICE_NAME = "aidr_commit_gateway_verdict_check"
SERVICE_PORT = 8788
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"

PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"

VERDICT_ALLOWED = ["TRUSTED", "REVIEWED"]
VERDICT_RESTRICTED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]

app = FastAPI()

def ws_query(sql: str) -> Dict[str, Any]:
    resp = requests.post(f"{QUERY_SERVICE_URL}/query", json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={"table": table, "rows": rows}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_execute(sql: str) -> Dict[str, Any]:
    resp = requests.post(f"{EXECUTE_SERVICE_URL}/execute", json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

def check_single_instance() -> bool:
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            return False
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def send_heartbeat():
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ")}])

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def get_server_by_mcp_name(mcp_name: str) -> Optional[Dict[str, Any]]:
    result = ws_query(f"SELECT server_id, name, verdict, trust_score FROM mcp_server_registry WHERE name = '{mcp_name}' LIMIT 1")
    rows = result.get("rows", [])
    return rows[0] if rows else None

def get_injection_resilience_score(server_id: str) -> Optional[float]:
    result = ws_query(f"SELECT score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience' ORDER BY scored_at DESC LIMIT 1")
    rows = result.get("rows", [])
    return float(rows[0]["score"]) if rows else None

def check_explicit_override(mcp_name: str) -> bool:
    result = ws_query(f"SELECT 1 FROM audit_log WHERE target_server_id IN (SELECT server_id FROM mcp_server_registry WHERE name = '{mcp_name}') AND event_type = 'manual_override' LIMIT 1")
    return len(result.get("rows", [])) > 0

def verify_verdict_enforcement(mcp_name: str, commit_payload: Dict[str, Any]) -> Dict[str, Any]:
    log(f"Verifying verdict enforcement for commit to {mcp_name}")
    
    server = get_server_by_mcp_name(mcp_name)
    if not server:
        return {"allowed": False, "reason": "server_not_found", "blocked": True}
    
    verdict = server.get("verdict", "UNKNOWN")
    log(f"Server {mcp_name} has verdict: {verdict}")
    
    if verdict in VERDICT_RESTRICTED:
        override = check_explicit_override(mcp_name)
        if not override:
            log(f"BLOCKED: verdict {verdict} requires explicit override for {mcp_name}")
            return {
                "allowed": False,
                "reason": f"verdict_{verdict}_restricted",
                "blocked": True,
                "verdict": verdict
            }
        else:
            log(f"OVERRIDE ACCEPTED: {mcp_name} has explicit override")
    
    if verdict not in VERDICT_ALLOWED and verdict not in VERDICT_RESTRICTED:
        return {"allowed": False, "reason": f"verdict_{verdict}_not_allowed", "blocked": True}
    
    inj_score = get_injection_resilience_score(server["server_id"])
    if inj_score is not None:
        commit_payload["injection_resilience_score"] = inj_score
        log(f"Added injection_resilience_score: {inj_score} to commit payload")
    
    return {"allowed": True, "reason": "verdict_allowed", "blocked": False, "verdict": verdict, "commit_payload": commit_payload}

def create_test_servers():
    log("Creating test servers for verification")
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry (
            server_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            verdict VARCHAR,
            trust_score DOUBLE,
            url VARCHAR,
            description VARCHAR
        )
    """)
    
    ws_execute("""
        CREATE SEQUENCE IF NOT EXISTS server_id_seq
    """)
    
    test_servers = [
        ("srv-trusted-001", "test-trusted-server", "TRUSTED", 0.95),
        ("srv-reviewed-001", "test-reviewed-server", "REVIEWED", 0.80),
        ("srv-caution-001", "test-caution-server", "CAUTION_LIMITED", 0.50),
        ("srv-highrisk-001", "test-highrisk-server", "HIGH_RISK_ISOLATED", 0.20),
        ("srv-unknown-001", "test-unknown-server", "UNKNOWN", 0.30),
    ]
    
    ws_execute("DELETE FROM mcp_server_registry WHERE name LIKE 'test-%'")
    
    for sid, name, verdict, trust in test_servers:
        ws_write("mcp_server_registry", [{"server_id": sid, "name": name, "verdict": verdict, "trust_score": trust}])

def create_test_signal_scores():
    log("Creating test signal scores")
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            evidence VARCHAR,
            scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    ws_execute("DELETE FROM mcp_signal_scores WHERE server_id LIKE 'srv-%'")
    
    signal_rows = [
        {"server_id": "srv-trusted-001", "signal_name": "injection_resilience", "score": 0.95},
        {"server_id": "srv-reviewed-001", "signal_name": "injection_resilience", "score": 0.85},
        {"server_id": "srv-caution-001", "signal_name": "injection_resilience", "score": 0.60},
        {"server_id": "srv-highrisk-001", "signal_name": "injection_resilience", "score": 0.30},
    ]
    
    ws_write("mcp_signal_scores", signal_rows)

def create_test_overrides():
    log("Creating test override records")
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            target_server_id VARCHAR,
            event_type VARCHAR,
            actor VARCHAR,
            detail VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    ws_execute("DELETE FROM audit_log WHERE target_server_id = 'srv-caution-001'")
    
    ws_write("audit_log", [
        {"id": 99999, "target_server_id": "srv-caution-001", "event_type": "manual_override", "actor": "admin@zo-sentinel", "detail": "explicit override for testing"}
    ])

def run_verification_tests() -> Dict[str, Any]:
    log("=" * 60)
    log("AIDR COMMIT GATEWAY VERDICT ENFORCEMENT VERIFICATION")
    log("=" * 60)
    
    results = {
        "tests_run": 0,
        "tests_passed": 0,
        "tests_failed": 0,
        "details": []
    }
    
    test_cases = [
        ("test-trusted-server", True, "TRUSTED verdict should be allowed"),
        ("test-reviewed-server", True, "REVIEWED verdict should be allowed"),
        ("test-caution-server", False, "CAUTION_LIMITED verdict should be blocked without override"),
        ("test-caution-server", True, "CAUTION_LIMITED with override should be allowed"),
        ("test-highrisk-server", False, "HIGH_RISK_ISOLATED should always be blocked"),
        ("test-unknown-server", False, "UNKNOWN verdict should be blocked"),
    ]
    
    for mcp_name, expected_allowed, description in test_cases:
        results["tests_run"] += 1
        
        if "override" in description and "caution" in description.lower():
            ws_write("audit_log", [{"id": 99998, "target_server_id": "srv-caution-001", "event_type": "manual_override", "actor": "admin@zo-sentinel", "detail": "test override for caution"}])
        
        payload = {"commit": {"message": "test commit", "author": "tester"}}
        result = verify_verdict_enforcement(mcp_name, payload)
        
        actual_allowed = result["allowed"]
        
        passed = actual_allowed == expected_allowed
        status = "PASS" if passed else "FAIL"
        
        log(f"[{status}] {description}")
        log(f"  Expected: allowed={expected_allowed}, Got: allowed={actual_allowed}")
        
        if result.get("injection_resilience_score"):
            log(f"  injection_resilience_score in payload: {result['injection_resilience_score']}")
        
        detail = {
            "test": description,
            "mcp_name": mcp_name,
            "expected_allowed": expected_allowed,
            "actual_allowed": actual_allowed,
            "passed": passed,
            "blocked": result.get("blocked", False),
            "reason": result.get("reason"),
            "verdict": result.get("verdict"),
            "injection_resilience_in_payload": result.get("injection_resilience_score") is not None
        }
        results["details"].append(detail)
        
        if passed:
            results["tests_passed"] += 1
        else:
            results["tests_failed"] += 1
    
    log("=" * 60)
    log(f"VERIFICATION RESULTS: {results['tests_passed']}/{results['tests_run']} passed")
    log("=" * 60)
    
    return results

@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}

@app.post("/verify")
def verify_endpoint(body: Dict[str, Any]):
    mcp_name = body.get("mcp_name")
    commit_payload = body.get("commit_payload", {})
    
    if not mcp_name:
        raise HTTPException(status_code=400, detail="mcp_name required")
    
    result = verify_verdict_enforcement(mcp_name, commit_payload)
    return result

@app.post("/run-tests")
def run_tests():
    create_test_servers()
    create_test_signal_scores()
    create_test_overrides()
    results = run_verification_tests()
    return results

def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log(f"Heartbeat failed: {e}")
        time.sleep(60)

def run():
    import os
    import threading
    
    if not check_single_instance():
        log(f"Another instance of {SERVICE_NAME} is running. Exiting.")
        return
    
    log(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT)

if __name__ == "__main__":
    run()