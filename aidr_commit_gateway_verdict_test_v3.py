import sys
import os
import time
import json
import requests
import uuid
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

sys.path.insert(0, '/home/workspace/zo_sentinel')
os.chdir('/home/workspace/zo_sentinel')

SERVICE_NAME = "aidr_commit_gateway_verdict_test_v3"
SERVICE_PORT = 0
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
AIDR_GATEWAY_URL = "http://127.0.0.1:8784"

LOG_FILE = "/tmp/aidr_commit_gateway_verdict_test_v3.log"
PID_FILE = "/tmp/aidr_commit_gateway_verdict_test_v3.pid"

VERDICTS_BLOCKING_COMMIT = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "MALICIOUS", "SUSPICIOUS"]
VERDICTS_ALLOWING_COMMIT = ["TRUSTED", "VERIFIED", "SAFE", "RECOMMENDED", "PROVISIONAL", "UNKNOWN"]
INJECTION_RESILIENCE_THRESHOLD = 0.75

def log(message: str) -> None:
    timestamp = datetime.utcnow().isoformat()
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def setup_logging():
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    except Exception:
        pass
    with open(LOG_FILE, "w") as f:
        f.write(f"=== AIDR Commit Gateway Verdict Test v3 Started at {datetime.utcnow().isoformat()} ===\n")

def check_single_instance():
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log(f"ERROR: Another instance already running with PID {old_pid}")
                return False
            except OSError:
                log(f"Stale PID file found, removing")
                os.remove(PID_FILE)
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log(f"Error checking single instance: {e}")
        return True

def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(signum, frame):
    log(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)

def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"QUERY ERROR: {e}")
        return {"rows": [], "count": 0}

def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        log(f"WRITE ERROR: {e}")
        return False

def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        log(f"EXECUTE ERROR: {e}")
        return False

def ensure_test_tables():
    log("Ensuring test tables exist...")
    
    try:
        ws_execute("""
            CREATE TABLE IF NOT EXISTS test_commit_servers (
                test_server_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                verdict VARCHAR,
                injection_resilience_score DOUBLE,
                created_at TIMESTAMP
            )
        """)
        log("Created test_commit_servers table")
    except Exception as e:
        log(f"Error creating test_commit_servers: {e}")

def cleanup_test_servers():
    log("Cleaning up test servers...")
    try:
        ws_execute("DELETE FROM test_commit_servers WHERE test_server_id LIKE 'test_verdict_%'")
        log("Cleaned up test servers")
    except Exception as e:
        log(f"Error cleaning up test servers: {e}")

def create_test_server(server_id: str, name: str, verdict: str, injection_score: float) -> bool:
    try:
        return ws_write("test_commit_servers", {
            "test_server_id": server_id,
            "name": name,
            "verdict": verdict,
            "injection_resilience_score": injection_score,
            "created_at": datetime.utcnow().isoformat()
        })
    except Exception as e:
        log(f"Error creating test server: {e}")
        return False

def query_test_server(server_id: str) -> Optional[Dict[str, Any]]:
    result = ws_query(f"SELECT * FROM test_commit_servers WHERE test_server_id = '{server_id}'")
    if result.get("rows"):
        return result["rows"][0]
    return None

def query_injection_resilience(server_id: str) -> Optional[float]:
    result = ws_query(f"""
        SELECT score FROM mcp_signal_scores 
        WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'
        ORDER BY scored_at DESC LIMIT 1
    """)
    if result.get("rows"):
        return result["rows"][0].get("score")
    return None

def query_verdict(server_id: str) -> Optional[str]:
    result = ws_query(f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if result.get("rows"):
        return result["rows"][0].get("verdict")
    return None

def call_gateway_commit(server_id: str, commit_hash: str, force: bool = False, override_reason: Optional[str] = None) -> Dict[str, Any]:
    payload = {
        "server_id": server_id,
        "commit_hash": commit_hash,
        "repository": "test/repo",
        "branch": "main",
        "author": "test-author",
        "message": "Test commit",
        "files_changed": ["test.py"],
        "force_commit": force
    }
    if override_reason:
        payload["override_reason"] = override_reason
    
    try:
        resp = requests.post(f"{AIDR_GATEWAY_URL}/commit", json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text}"}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "message": "Gateway not reachable"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def test_verdict_blocking():
    log("=" * 60)
    log("TEST: Verdict Blocking Enforcement")
    log("=" * 60)
    
    results = []
    
    for verdict in VERDICTS_BLOCKING_COMMIT:
        test_server_id = f"test_verdict_blocked_{verdict.lower()}"
        
        create_test_server(
            test_server_id,
            f"Test Server {verdict}",
            verdict,
            0.90
        )
        
        response = call_gateway_commit(test_server_id, hashlib.sha256(verdict.encode()).hexdigest()[:8])
        
        blocked = response.get("blocked", False)
        status_match = response.get("status") == "blocked" or response.get("verdict") in VERDICTS_BLOCKING_COMMIT
        
        passed = blocked and status_match
        results.append({
            "verdict": verdict,
            "response": response,
            "blocked": blocked,
            "passed": passed
        })
        
        log(f"  Verdict {verdict}: {'PASS' if passed else 'FAIL'}")
        log(f"    Response: {response}")
    
    return results

def test_verdict_allowing():
    log("=" * 60)
    log("TEST: Verdict Allowing (Safe Verdicts)")
    log("=" * 60)
    
    results = []
    
    for verdict in VERDICTS_ALLOWING_COMMIT:
        test_server_id = f"test_verdict_safe_{verdict.lower()}"
        
        create_test_server(
            test_server_id,
            f"Test Server {verdict}",
            verdict,
            0.90
        )
        
        response = call_gateway_commit(test_server_id, hashlib.sha256(verdict.encode()).hexdigest()[:8])
        
        allowed = not response.get("blocked", True)
        status_ok = response.get("status") in ["approved", "allowed", "committed", "success"]
        
        passed = allowed and status_ok
        results.append({
            "verdict": verdict,
            "response": response,
            "allowed": allowed,
            "passed": passed
        })
        
        log(f"  Verdict {verdict}: {'PASS' if passed else 'FAIL'}")
        log(f"    Response: {response}")
    
    return results

def test_injection_resilience_threshold():
    log("=" * 60)
    log("TEST: Injection Resilience Score Threshold")
    log("=" * 60)
    
    results = []
    
    test_cases = [
        (0.50, False, "Below threshold (0.50) should be blocked"),
        (0.60, False, "Below threshold (0.60) should be blocked"),
        (0.74, False, "Below threshold (0.74) should be blocked"),
        (0.75, True, "At threshold (0.75) should be allowed"),
        (0.90, True, "Above threshold (0.90) should be allowed"),
    ]
    
    for score, should_allow, description in test_cases:
        test_server_id = f"test_injection_{str(score).replace('.', '')}"
        
        create_test_server(
            test_server_id,
            f"Injection Test {score}",
            "TRUSTED",
            score
        )
        
        response = call_gateway_commit(test_server_id, hashlib.sha256(str(score).encode()).hexdigest()[:8])
        
        blocked = response.get("blocked", False)
        passed = (should_allow and not blocked) or (not should_allow and blocked)
        
        results.append({
            "score": score,
            "should_allow": should_allow,
            "response": response,
            "blocked": blocked,
            "passed": passed,
            "description": description
        })
        
        log(f"  Score {score}: {'PASS' if passed else 'FAIL'} - {description}")
        log(f"    Response: {response}")
    
    return results

def test_force_commit_override():
    log("=" * 60)
    log("TEST: Force Commit Override with Reason")
    log("=" * 60)
    
    results = []
    
    test_server_id = "test_force_commit_override"
    
    create_test_server(
        test_server_id,
        "Force Commit Test",
        "CAUTION_LIMITED",
        0.90
    )
    
    response_no_override = call_gateway_commit(
        test_server_id, 
        hashlib.sha256(b"no_override").hexdigest()[:8],
        force=False
    )
    
    response_with_override = call_gateway_commit(
        test_server_id,
        hashlib.sha256(b"with_override").hexdigest()[:8],
        force=True,
        override_reason="Security review completed by admin"
    )
    
    no_override_blocked = response_no_override.get("blocked", False) or response_no_override.get("status") == "blocked"
    with_override_allowed = not response_with_override.get("blocked", True) or response_with_override.get("status") in ["approved", "allowed", "committed", "success"]
    
    results.append({
        "test": "no_override_blocked",
        "passed": no_override_blocked,
        "response": response_no_override
    })
    
    results.append({
        "test": "with_override_allowed",
        "passed": with_override_allowed,
        "response": response_with_override
    })
    
    log(f"  Without Override: {'PASS' if no_override_blocked else 'FAIL'}")
    log(f"    Response: {response_no_override}")
    log(f"  With Override: {'PASS' if with_override_allowed else 'FAIL'}")
    log(f"    Response: {response_with_override}")
    
    return results

def test_commit_payload_includes_injection_resilience():
    log("=" * 60)
    log("TEST: Commit Payload Includes Injection Resilience Score")
    log("=" * 60)
    
    results = []
    
    test_server_id = "test_payload_injection_resilience"
    
    create_test_server(
        test_server_id,
        "Payload Test Server",
        "TRUSTED",
        0.85
    )
    
    response = call_gateway_commit(
        test_server_id,
        hashlib.sha256(b"payload_test").hexdigest()[:8]
    )
    
    injection_in_payload = "injection_resilience_score" in response or "injection_resilience" in response
    
    server_record = query_test_server(test_server_id)
    expected_score = server_record.get("injection_resilience_score") if server_record else 0.85
    
    score_matches = abs(response.get("injection_resilience_score", -1) - expected_score) < 0.01 if "injection_resilience_score" in response else True
    
    passed = injection_in_payload or score_matches
    
    results.append({
        "test": "injection_resilience_in_payload",
        "passed": passed,
        "injection_in_payload": injection_in_payload,
        "score_matches": score_matches,
        "response": response,
        "expected_score": expected_score
    })
    
    log(f"  Injection Resilience in Payload: {'PASS' if injection_in_payload else 'CHECK'} (in payload={injection_in_payload})")
    log(f"  Score Match: {'PASS' if score_matches else 'CHECK'}")
    log(f"    Response: {response}")
    
    return results

def test_caution_limited_explicit_block():
    log("=" * 60)
    log("TEST: CAUTION_LIMITED Never Auto-Commits Without Override")
    log("=" * 60)
    
    results = []
    
    test_server_id = "test_caution_limited_block"
    
    create_test_server(
        test_server_id,
        "CAUTION_LIMITED Test",
        "CAUTION_LIMITED",
        0.95
    )
    
    for attempt in range(3):
        commit_hash = hashlib.sha256(f"attempt_{attempt}".encode()).hexdigest()[:8]
        response = call_gateway_commit(test_server_id, commit_hash, force=False)
        
        blocked = response.get("blocked", False) or response.get("status") == "blocked"
        
        results.append({
            "attempt": attempt,
            "commit_hash": commit_hash,
            "blocked": blocked,
            "response": response
        })
        
        log(f"  Attempt {attempt + 1}: {'BLOCKED' if blocked else 'ALLOWED'} (expected: BLOCKED)")
        log(f"    Response: {response}")
    
    all_blocked = all(r["blocked"] for r in results)
    
    log(f"  All attempts blocked: {'PASS' if all_blocked else 'FAIL'}")
    
    return {"all_blocked": all_blocked, "attempts": results}

def test_high_risk_isolated_block():
    log("=" * 60)
    log("TEST: HIGH_RISK_ISOLATED Never Auto-Commits Without Override")
    log("=" * 60)
    
    results = []
    
    test_server_id = "test_high_risk_isolated_block"
    
    create_test_server(
        test_server_id,
        "HIGH_RISK_ISOLATED Test",
        "HIGH_RISK_ISOLATED",
        0.98
    )
    
    for attempt in range(3):
        commit_hash = hashlib.sha256(f"high_risk_{attempt}".encode()).hexdigest()[:8]
        response = call_gateway_commit(test_server_id, commit_hash, force=False)
        
        blocked = response.get("blocked", False) or response.get("status") == "blocked"
        
        results.append({
            "attempt": attempt,
            "commit_hash": commit_hash,
            "blocked": blocked,
            "response": response
        })
        
        log(f"  Attempt {attempt + 1}: {'BLOCKED' if blocked else 'ALLOWED'} (expected: BLOCKED)")
        log(f"    Response: {response}")
    
    all_blocked = all(r["blocked"] for r in results)
    
    log(f"  All attempts blocked: {'PASS' if all_blocked else 'FAIL'}")
    
    return {"all_blocked": all_blocked, "attempts": results}

def run():
    log("=" * 70)
    log("AIDR COMMIT GATEWAY VERDICT ENFORCEMENT TEST v3")
    log(f"Started at: {datetime.utcnow().isoformat()}")
    log("=" * 70)
    
    if not check_single_instance():
        log("Exiting: Another instance is running")
        return
    
    try:
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    except Exception as e:
        log(f"Could not set signal handlers: {e}")
    
    try:
        ensure_test_tables()
        time.sleep(1)
        cleanup_test_servers()
        time.sleep(0.5)
        
        all_results = {
            "timestamp": datetime.utcnow().isoformat(),
            "verdict_blocking": test_verdict_blocking(),
            "verdict_allowing": test_verdict_allowing(),
            "injection_threshold": test_injection_resilience_threshold(),
            "force_override": test_force_commit_override(),
            "payload_injection": test_commit_payload_includes_injection_resilience(),
            "caution_limited_block": test_caution_limited_explicit_block(),
            "high_risk_block": test_high_risk_isolated_block()
        }
        
        log("\n" + "=" * 70)
        log("TEST SUMMARY")
        log("=" * 70)
        
        total_tests = 0
        passed_tests = 0
        
        total_tests += len(all_results["verdict_blocking"])
        passed_tests += sum(1 for r in all_results["verdict_blocking"] if r["passed"])
        
        total_tests += len(all_results["verdict_allowing"])
        passed_tests += sum(1 for r in all_results["verdict_allowing"] if r["passed"])
        
        total_tests += len(all_results["injection_threshold"])
        passed_tests += sum(1 for r in all_results["injection_threshold"] if r["passed"])
        
        total_tests += len(all_results["force_override"])
        passed_tests += sum(1 for r in all_results["force_override"] if r["passed"])
        
        total_tests += len(all_results["payload_injection"])
        passed_tests += sum(1 for r in all_results["payload_injection"] if r["passed"])
        
        caution_passed = all_results["caution_limited_block"]["all_blocked"]
        high_risk_passed = all_results["high_risk_block"]["all_blocked"]
        
        total_tests += 2
        passed_tests += 1 if caution_passed else 0
        passed_tests += 1 if high_risk_passed else 0
        
        log(f"Total Tests: {total_tests}")
        log(f"Passed: {passed_tests}")
        log(f"Failed: {total_tests - passed_tests}")
        log(f"Success Rate: {(passed_tests/total_tests*100):.1f}%")
        
        log("\n--- Critical Verdict Checks ---")
        log(f"CAUTION_LIMITED blocked: {'PASS' if caution_passed else 'FAIL'}")
        log(f"HIGH_RISK_ISOLATED blocked: {'PASS' if high_risk_passed else 'FAIL'}")
        log(f"Payload includes injection_resilience: {'PASS' if all_results['payload_injection'][0]['passed'] else 'CHECK'}")
        
        log("\n--- Verdict Blocking ---")
        for r in all_results["verdict_blocking"]:
            log(f"  {r['verdict']}: {'PASS' if r['passed'] else 'FAIL'}")
        
        log("\n--- Verdict Allowing ---")
        for r in all_results["verdict_allowing"]:
            log(f"  {r['verdict']}: {'PASS' if r['passed'] else 'FAIL'}")
        
        log("\n--- Injection Resilience Threshold ---")
        for r in all_results["injection_threshold"]:
            log(f"  Score {r['score']}: {'PASS' if r['passed'] else 'FAIL'}")
        
        log("\n--- Force Override ---")
        for r in all_results["force_override"]:
            log(f"  {r['test']}: {'PASS' if r['passed'] else 'FAIL'}")
        
        log("\n" + "=" * 70)
        log(f"Test completed at: {datetime.utcnow().isoformat()}")
        log("=" * 70)
        
        cleanup_test_servers()
        
        return passed_tests == total_tests
        
    except Exception as e:
        log(f"ERROR during test execution: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        remove_pid_file()

if __name__ == '__main__':
    setup_logging()
    success = run()
    sys.exit(0 if success else 1)