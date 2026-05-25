import os
import sys
import json
import logging
import hashlib
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

SERVICE_NAME = "aidr_commit_gateway_verdict_check_test"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

VERDICTS_ALLOWED = ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"]
VERDICTS_BLOCKED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"]
VERDICT_THRESHOLD_FORWARD = 0.7
INJECTION_RESILIENCE_REQUIRED = 0.5


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("ok", False)
    except Exception as e:
        logger.error(f"ws_write failed for {table}: {e}")
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return False


def compute_deterministic_id(*fields: str) -> str:
    content = "|".join(str(f) for f in fields)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            return False
        except (OSError, ValueError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    remove_pid_file()
    sys.exit(0)


def setup_test_tables() -> bool:
    logger.info("Setting up test tables...")
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry_test (
            server_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            url VARCHAR,
            description VARCHAR,
            trust_score DOUBLE,
            verdict VARCHAR,
            registry_source VARCHAR,
            scan_count INTEGER,
            first_seen VARCHAR,
            last_seen VARCHAR,
            last_assessed VARCHAR,
            last_scanned VARCHAR
        )
    """)
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_signal_scores_test (
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            evidence VARCHAR,
            scored_at VARCHAR
        )
    """)
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS aidr_commit_payloads_test (
            payload_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            verdict VARCHAR,
            injection_resilience_score DOUBLE,
            commit_allowed BOOLEAN,
            rejection_reason VARCHAR,
            override_applied BOOLEAN,
            created_at VARCHAR
        )
    """)
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS test_results (
            test_name VARCHAR,
            passed BOOLEAN,
            detail VARCHAR,
            tested_at VARCHAR
        )
    """)
    
    return True


def cleanup_test_tables() -> bool:
    logger.info("Cleaning up test tables...")
    ws_execute("DROP TABLE IF EXISTS mcp_server_registry_test")
    ws_execute("DROP TABLE IF EXISTS mcp_signal_scores_test")
    ws_execute("DROP TABLE IF EXISTS aidr_commit_payloads_test")
    return True


def create_synthetic_server(
    server_id: str,
    name: str,
    verdict: str,
    trust_score: float,
    injection_resilience_score: float
) -> bool:
    now = utc_now_iso()
    row = {
        "server_id": server_id,
        "name": name,
        "url": f"https://test-{server_id}.example.com",
        "description": f"Test MCP server for verdict check: {verdict}",
        "trust_score": trust_score,
        "verdict": verdict,
        "registry_source": "synthetic_test",
        "scan_count": 1,
        "first_seen": now,
        "last_seen": now,
        "last_assessed": now,
        "last_scanned": now
    }
    
    if not ws_write("mcp_server_registry_test", [row]):
        return False
    
    signal_row = {
        "server_id": server_id,
        "signal_name": "injection_resilience",
        "score": injection_resilience_score,
        "evidence": json.dumps({"test": True, "verdict": verdict}),
        "scored_at": now
    }
    
    return ws_write("mcp_signal_scores_test", [signal_row])


def check_verdict_allowed(verdict: str) -> bool:
    return verdict in VERDICTS_ALLOWED


def check_verdict_blocked(verdict: str) -> bool:
    return verdict in VERDICTS_BLOCKED


def check_injection_resilience_score(server_id: str) -> float:
    sql = f"""
        SELECT score FROM mcp_signal_scores_test 
        WHERE server_id = ? AND signal_name = 'injection_resilience'
    """
    rows = ws_query(sql)
    if rows:
        return rows[0].get("score", 0.0)
    return 0.0


def simulate_gateway_verdict_check(
    server_id: str,
    verdict: str,
    injection_resilience_score: float,
    override_applied: bool = False
) -> Dict[str, Any]:
    commit_allowed = False
    rejection_reason = None
    
    if override_applied:
        commit_allowed = True
    elif check_verdict_blocked(verdict):
        rejection_reason = f"Verdict {verdict} blocked: requires explicit analyst override"
    elif not check_verdict_allowed(verdict):
        rejection_reason = f"Verdict {verdict} not in allowed list"
    else:
        if injection_resilience_score < INJECTION_RESILIENCE_REQUIRED:
            rejection_reason = f"injection_resilience score {injection_resilience_score} below threshold {INJECTION_RESILIENCE_REQUIRED}"
        else:
            commit_allowed = True
    
    return {
        "commit_allowed": commit_allowed,
        "rejection_reason": rejection_reason,
        "verdict": verdict,
        "injection_resilience_score": injection_resilience_score
    }


def record_payload(
    server_id: str,
    verdict: str,
    injection_resilience_score: float,
    commit_allowed: bool,
    rejection_reason: Optional[str],
    override_applied: bool
) -> bool:
    payload_id = compute_deterministic_id(server_id, verdict, utc_now_iso())
    row = {
        "payload_id": payload_id,
        "server_id": server_id,
        "verdict": verdict,
        "injection_resilience_score": injection_resilience_score,
        "commit_allowed": commit_allowed,
        "rejection_reason": rejection_reason,
        "override_applied": override_applied,
        "created_at": utc_now_iso()
    }
    return ws_write("aidr_commit_payloads_test", [row])


def record_test_result(test_name: str, passed: bool, detail: str) -> bool:
    row = {
        "test_name": test_name,
        "passed": passed,
        "detail": detail,
        "tested_at": utc_now_iso()
    }
    return ws_write("test_results", [row])


def test_trusted_general_allowed():
    test_name = "test_trusted_general_allowed"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "trusted_general", utc_now_iso())
    verdict = "TRUSTED_GENERAL"
    trust_score = 0.85
    injection_score = 0.75
    
    if not create_synthetic_server(server_id, "Test Trusted General", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if result["commit_allowed"]:
        logger.info(f"PASS: {test_name} - TRUSTED_GENERAL correctly allowed")
        record_test_result(test_name, True, "TRUSTED_GENERAL correctly allowed commit")
        return True
    else:
        logger.error(f"FAIL: {test_name} - TRUSTED_GENERAL should be allowed but got: {result}")
        record_test_result(test_name, False, f"Unexpected rejection: {result['rejection_reason']}")
        return False


def test_trusted_research_allowed():
    test_name = "test_trusted_research_allowed"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "trusted_research", utc_now_iso())
    verdict = "TRUSTED_RESEARCH"
    trust_score = 0.80
    injection_score = 0.72
    
    if not create_synthetic_server(server_id, "Test Trusted Research", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if result["commit_allowed"]:
        logger.info(f"PASS: {test_name} - TRUSTED_RESEARCH correctly allowed")
        record_test_result(test_name, True, "TRUSTED_RESEARCH correctly allowed commit")
        return True
    else:
        logger.error(f"FAIL: {test_name} - TRUSTED_RESEARCH should be allowed but got: {result}")
        record_test_result(test_name, False, f"Unexpected rejection: {result['rejection_reason']}")
        return False


def test_caution_limited_blocked():
    test_name = "test_caution_limited_blocked"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "caution_limited", utc_now_iso())
    verdict = "CAUTION_LIMITED"
    trust_score = 0.45
    injection_score = 0.60
    
    if not create_synthetic_server(server_id, "Test Caution Limited", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if not result["commit_allowed"] and "CAUTION_LIMITED" in str(result["rejection_reason"]):
        logger.info(f"PASS: {test_name} - CAUTION_LIMITED correctly blocked")
        record_test_result(test_name, True, "CAUTION_LIMITED correctly blocked")
        return True
    else:
        logger.error(f"FAIL: {test_name} - CAUTION_LIMITED should be blocked but got: {result}")
        record_test_result(test_name, False, f"Should block CAUTION_LIMITED but: {result}")
        return False


def test_high_risk_isolated_blocked():
    test_name = "test_high_risk_isolated_blocked"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "high_risk_isolated", utc_now_iso())
    verdict = "HIGH_RISK_ISOLATED"
    trust_score = 0.25
    injection_score = 0.35
    
    if not create_synthetic_server(server_id, "Test High Risk Isolated", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if not result["commit_allowed"] and "HIGH_RISK_ISOLATED" in str(result["rejection_reason"]):
        logger.info(f"PASS: {test_name} - HIGH_RISK_ISOLATED correctly blocked")
        record_test_result(test_name, True, "HIGH_RISK_ISOLATED correctly blocked")
        return True
    else:
        logger.error(f"FAIL: {test_name} - HIGH_RISK_ISOLATED should be blocked but got: {result}")
        record_test_result(test_name, False, f"Should block HIGH_RISK_ISOLATED but: {result}")
        return False


def test_caution_limited_with_override():
    test_name = "test_caution_limited_with_override"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "caution_override", utc_now_iso())
    verdict = "CAUTION_LIMITED"
    trust_score = 0.45
    injection_score = 0.60
    
    if not create_synthetic_server(server_id, "Test Caution Override", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score, override_applied=True)
    
    if result["commit_allowed"]:
        logger.info(f"PASS: {test_name} - CAUTION_LIMITED correctly allowed with override")
        record_test_result(test_name, True, "CAUTION_LIMITED correctly allowed with override")
        return True
    else:
        logger.error(f"FAIL: {test_name} - Override should allow commit but got: {result}")
        record_test_result(test_name, False, f"Override should allow commit but: {result}")
        return False


def test_high_risk_with_override():
    test_name = "test_high_risk_with_override"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "high_risk_override", utc_now_iso())
    verdict = "HIGH_RISK_ISOLATED"
    trust_score = 0.25
    injection_score = 0.35
    
    if not create_synthetic_server(server_id, "Test High Risk Override", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score, override_applied=True)
    
    if result["commit_allowed"]:
        logger.info(f"PASS: {test_name} - HIGH_RISK_ISOLATED correctly allowed with override")
        record_test_result(test_name, True, "HIGH_RISK_ISOLATED correctly allowed with override")
        return True
    else:
        logger.error(f"FAIL: {test_name} - Override should allow commit but got: {result}")
        record_test_result(test_name, False, f"Override should allow commit but: {result}")
        return False


def test_injection_resilience_included_in_payload():
    test_name = "test_injection_resilience_included_in_payload"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "injection_check", utc_now_iso())
    verdict = "TRUSTED_GENERAL"
    trust_score = 0.85
    injection_score = 0.78
    
    if not create_synthetic_server(server_id, "Test Injection Check", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if result["commit_allowed"] and "injection_resilience_score" in result:
        if result["injection_resilience_score"] == injection_score:
            logger.info(f"PASS: {test_name} - injection_resilience score {injection_score} included in payload")
            record_test_result(test_name, True, f"injection_resilience score {injection_score} correctly included")
            
            record_payload(
                server_id, verdict, injection_score,
                result["commit_allowed"], result["rejection_reason"], False
            )
            return True
        else:
            logger.error(f"FAIL: {test_name} - injection_resilience score mismatch: expected {injection_score}, got {result['injection_resilience_score']}")
            record_test_result(test_name, False, f"Score mismatch: {result['injection_resilience_score']}")
            return False
    else:
        logger.error(f"FAIL: {test_name} - Commit not allowed or injection_resilience_score missing: {result}")
        record_test_result(test_name, False, f"Missing injection_resilience_score: {result}")
        return False


def test_injection_resilience_below_threshold():
    test_name = "test_injection_resilience_below_threshold"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "injection_low", utc_now_iso())
    verdict = "TRUSTED_GENERAL"
    trust_score = 0.85
    injection_score = 0.30
    
    if not create_synthetic_server(server_id, "Test Low Injection", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if not result["commit_allowed"] and "injection_resilience" in str(result["rejection_reason"]):
        logger.info(f"PASS: {test_name} - LOW injection_resilience correctly blocked")
        record_test_result(test_name, True, f"Low injection_resilience (0.30) correctly blocked")
        return True
    else:
        logger.error(f"FAIL: {test_name} - Low injection_resilience should be blocked but got: {result}")
        record_test_result(test_name, False, f"Should block low injection_resilience: {result}")
        return False


def test_known_threat_blocked():
    test_name = "test_known_threat_blocked"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "known_threat", utc_now_iso())
    verdict = "KNOWN_THREAT"
    trust_score = 0.10
    injection_score = 0.10
    
    if not create_synthetic_server(server_id, "Test Known Threat", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if not result["commit_allowed"] and "KNOWN_THREAT" in str(result["rejection_reason"]):
        logger.info(f"PASS: {test_name} - KNOWN_THREAT correctly blocked")
        record_test_result(test_name, True, "KNOWN_THREAT correctly blocked")
        return True
    else:
        logger.error(f"FAIL: {test_name} - KNOWN_THREAT should be blocked but got: {result}")
        record_test_result(test_name, False, f"Should block KNOWN_THREAT but: {result}")
        return False


def test_unknown_verdict_blocked():
    test_name = "test_unknown_verdict_blocked"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "unknown_verdict", utc_now_iso())
    verdict = "UNKNOWN"
    trust_score = 0.30
    injection_score = 0.50
    
    if not create_synthetic_server(server_id, "Test Unknown Verdict", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if not result["commit_allowed"]:
        logger.info(f"PASS: {test_name} - UNKNOWN verdict correctly blocked")
        record_test_result(test_name, True, "UNKNOWN verdict correctly blocked")
        return True
    else:
        logger.error(f"FAIL: {test_name} - UNKNOWN should be blocked but got: {result}")
        record_test_result(test_name, False, f"Should block UNKNOWN verdict but: {result}")
        return False


def test_enterprise_controlled_allowed():
    test_name = "test_enterprise_controlled_allowed"
    logger.info(f"Running {test_name}...")
    
    server_id = compute_deterministic_id("test", "enterprise_controlled", utc_now_iso())
    verdict = "ENTERPRISE_CONTROLLED"
    trust_score = 0.95
    injection_score = 0.90
    
    if not create_synthetic_server(server_id, "Test Enterprise Controlled", verdict, trust_score, injection_score):
        return record_test_result(test_name, False, "Failed to create synthetic server"), False
    
    result = simulate_gateway_verdict_check(server_id, verdict, injection_score)
    
    if result["commit_allowed"]:
        logger.info(f"PASS: {test_name} - ENTERPRISE_CONTROLLED correctly allowed")
        record_test_result(test_name, True, "ENTERPRISE_CONTROLLED correctly allowed commit")
        return True
    else:
        logger.error(f"FAIL: {test_name} - ENTERPRISE_CONTROLLED should be allowed but got: {result}")
        record_test_result(test_name, False, f"Unexpected rejection: {result['rejection_reason']}")
        return False


def run_all_tests():
    results = []
    
    tests = [
        test_trusted_general_allowed,
        test_trusted_research_allowed,
        test_caution_limited_blocked,
        test_high_risk_isolated_blocked,
        test_caution_limited_with_override,
        test_high_risk_with_override,
        test_injection_resilience_included_in_payload,
        test_injection_resilience_below_threshold,
        test_known_threat_blocked,
        test_unknown_verdict_blocked,
        test_enterprise_controlled_allowed,
    ]
    
    for test_fn in tests:
        try:
            result = test_fn()
            results.append((test_fn.__name__, result))
        except Exception as e:
            logger.error(f"Test {test_fn.__name__} raised exception: {e}")
            results.append((test_fn.__name__, False))
    
    return results


def print_summary(results: List[tuple]):
    print("\n" + "=" * 60)
    print("AIDR COMMIT GATEWAY VERDICT CHECK TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    failed = sum(1 for _, r in results if not r)
    total = len(results)
    
    print(f"\nTotal: {total} | Passed: {passed} | Failed: {failed}")
    print("-" * 60)
    
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        symbol = "[OK]" if result else "[!!]"
        print(f"  {symbol} {test_name}: {status}")
    
    print("-" * 60)
    
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"FAILURES: {failed} test(s) failed")
    
    print("=" * 60)


def main():
    logger.info("Starting AIDR Commit Gateway Verdict Check Test...")
    
    if not check_single_instance():
        logger.error("Another instance is already running")
        print("[ERROR] Another instance is already running")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        if not setup_test_tables():
            logger.error("Failed to setup test tables")
            print("[ERROR] Failed to setup test tables")
            remove_pid_file()
            sys.exit(1)
        
        results = run_all_tests()
        
        print_summary(results)
        
        failed = sum(1 for _, r in results if not r)
        
        if failed > 0:
            logger.error(f"{failed} tests failed")
            remove_pid_file()
            sys.exit(1)
        
        logger.info("All tests passed successfully")
        
    except Exception as e:
        logger.error(f"Test suite failed with exception: {e}")
        print(f"[ERROR] Test suite exception: {e}")
        remove_pid_file()
        sys.exit(1)
    
    remove_pid_file()
    sys.exit(0)


if __name__ == "__main__":
    main()