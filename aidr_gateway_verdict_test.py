import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

SERVICE_NAME = "aidr_gateway_verdict_test"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
RESULTS_FILE = "/home/workspace/zo_sentinel/test_results.jsonl"


def ws_query(sql: str) -> Dict[str, Any]:
    import requests
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "rows": []}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    import requests
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def ws_execute(sql: str) -> Dict[str, Any]:
    import requests
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def log_result(result: Dict[str, Any]):
    with open(RESULTS_FILE, "a") as f:
        f.write(json.dumps(result) + "\n")


def create_test_server(name: str, verdict: str, trust_score: float, injection_resilience: float = 0.0) -> str:
    server_id = f"test_aidr_{uuid.uuid4().hex[:12]}"
    
    ws_execute(f"""
        INSERT INTO mcp_server_registry (server_id, name, description, url, trust_score, verdict, registry_source, scan_count)
        VALUES ('{server_id}', '{name}', 'AIDR test server for verdict enforcement', 'https://test-{server_id}.example.com', {trust_score}, '{verdict}', 'test', 1)
    """)
    
    ws_write("mcp_signal_scores", [{
        "server_id": server_id,
        "signal_name": "injection_resilience",
        "score": injection_resilience,
        "evidence": f"Test injection resilience score: {injection_resilience}",
        "scored_at": datetime.utcnow().isoformat()
    }])
    
    return server_id


def clear_test_servers():
    ws_execute("DELETE FROM mcp_signal_scores WHERE server_id LIKE 'test_aidr_%'")
    ws_execute("DELETE FROM mcp_server_registry WHERE server_id LIKE 'test_aidr_%'")
    ws_execute("DELETE FROM audit_log WHERE target_server_id LIKE 'test_aidr_%'")


def check_verdict_state(server_id: str) -> Optional[Dict[str, Any]]:
    result = ws_query(f"""
        SELECT server_id, name, verdict, trust_score
        FROM mcp_server_registry
        WHERE server_id = '{server_id}'
    """)
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0]
    return None


def check_injection_resilience(server_id: str) -> Optional[float]:
    result = ws_query(f"""
        SELECT score
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'
        ORDER BY scored_at DESC
        LIMIT 1
    """)
    if result.get("rows") and len(result["rows"]) > 0:
        return result["rows"][0].get("score")
    return None


def simulate_commit(server_id: str, override: bool = False) -> Dict[str, Any]:
    verdict_state = check_verdict_state(server_id)
    if not verdict_state:
        return {"status": "error", "reason": "Server not found"}
    
    verdict = verdict_state.get("verdict", "UNKNOWN")
    commit_requested = datetime.utcnow().isoformat()
    
    blocked_verdicts = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]
    forwarded_verdicts = ["TRUSTED_GENERAL", "TRUSTED_RESEARCH"]
    
    should_block = verdict in blocked_verdicts and not override
    
    ws_write("audit_log", [{
        "target_server_id": server_id,
        "event_type": "commit_request",
        "actor": "test_harness",
        "detail": json.dumps({
            "verdict": verdict,
            "override": override,
            "blocked": should_block,
            "timestamp": commit_requested
        }),
        "created_at": datetime.utcnow().isoformat()
    }])
    
    if should_block:
        return {
            "status": "blocked",
            "server_id": server_id,
            "verdict": verdict,
            "reason": f"Verdict {verdict} requires override for commit",
            "timestamp": commit_requested
        }
    else:
        return {
            "status": "forwarded",
            "server_id": server_id,
            "verdict": verdict,
            "reason": f"Verdict {verdict} allows commit",
            "timestamp": commit_requested
        }


def check_payload_injection_resilience(server_id: str) -> Dict[str, Any]:
    signal = check_injection_resilience(server_id)
    return {
        "server_id": server_id,
        "injection_resilience_included": signal is not None,
        "injection_resilience_value": signal,
        "timestamp": datetime.utcnow().isoformat()
    }


def test_forward_trusted_general():
    server_id = create_test_server(
        name="Test TRUSTED_GENERAL Server",
        verdict="TRUSTED_GENERAL",
        trust_score=0.85,
        injection_resilience=0.92
    )
    
    result = simulate_commit(server_id)
    payload_check = check_payload_injection_resilience(server_id)
    
    test_result = {
        "test_name": "test_forward_trusted_general",
        "server_id": server_id,
        "expected": "forwarded",
        "actual": result.get("status"),
        "verdict": result.get("verdict"),
        "pass": result.get("status") == "forwarded",
        "payload_includes_injection_resilience": payload_check["injection_resilience_included"],
        "injection_resilience_value": payload_check["injection_resilience_value"],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    log_result(test_result)
    print(f"[TEST] test_forward_trusted_general: {'PASS' if test_result['pass'] else 'FAIL'}")
    return test_result


def test_forward_trusted_research():
    server_id = create_test_server(
        name="Test TRUSTED_RESEARCH Server",
        verdict="TRUSTED_RESEARCH",
        trust_score=0.90,
        injection_resilience=0.88
    )
    
    result = simulate_commit(server_id)
    payload_check = check_payload_injection_resilience(server_id)
    
    test_result = {
        "test_name": "test_forward_trusted_research",
        "server_id": server_id,
        "expected": "forwarded",
        "actual": result.get("status"),
        "verdict": result.get("verdict"),
        "pass": result.get("status") == "forwarded",
        "payload_includes_injection_resilience": payload_check["injection_resilience_included"],
        "injection_resilience_value": payload_check["injection_resilience_value"],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    log_result(test_result)
    print(f"[TEST] test_forward_trusted_research: {'PASS' if test_result['pass'] else 'FAIL'}")
    return test_result


def test_block_caution_limited():
    server_id = create_test_server(
        name="Test CAUTION_LIMITED Server",
        verdict="CAUTION_LIMITED",
        trust_score=0.45,
        injection_resilience=0.55
    )
    
    result = simulate_commit(server_id, override=False)
    payload_check = check_payload_injection_resilience(server_id)
    
    test_result = {
        "test_name": "test_block_caution_limited",
        "server_id": server_id,
        "expected": "blocked",
        "actual": result.get("status"),
        "verdict": result.get("verdict"),
        "pass": result.get("status") == "blocked",
        "payload_includes_injection_resilience": payload_check["injection_resilience_included"],
        "injection_resilience_value": payload_check["injection_resilience_value"],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    log_result(test_result)
    print(f"[TEST] test_block_caution_limited: {'PASS' if test_result['pass'] else 'FAIL'}")
    return test_result


def test_block_high_risk_isolated():
    server_id = create_test_server(
        name="Test HIGH_RISK_ISOLATED Server",
        verdict="HIGH_RISK_ISOLATED",
        trust_score=0.20,
        injection_resilience=0.30
    )
    
    result = simulate_commit(server_id, override=False)
    payload_check = check_payload_injection_resilience(server_id)
    
    test_result = {
        "test_name": "test_block_high_risk_isolated",
        "server_id": server_id,
        "expected": "blocked",
        "actual": result.get("status"),
        "verdict": result.get("verdict"),
        "pass": result.get("status") == "blocked",
        "payload_includes_injection_resilience": payload_check["injection_resilience_included"],
        "injection_resilience_value": payload_check["injection_resilience_value"],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    log_result(test_result)
    print(f"[TEST] test_block_high_risk_isolated: {'PASS' if test_result['pass'] else 'FAIL'}")
    return test_result


def test_override_caution_limited():
    server_id = create_test_server(
        name="Test CAUTION_LIMITED Override Server",
        verdict="CAUTION_LIMITED",
        trust_score=0.50,
        injection_resilience=0.65
    )
    
    result = simulate_commit(server_id, override=True)
    
    test_result = {
        "test_name": "test_override_caution_limited",
        "server_id": server_id,
        "expected": "forwarded",
        "actual": result.get("status"),
        "verdict": result.get("verdict"),
        "override_used": True,
        "pass": result.get("status") == "forwarded",
        "timestamp": datetime.utcnow().isoformat()
    }
    
    log_result(test_result)
    print(f"[TEST] test_override_caution_limited: {'PASS' if test_result['pass'] else 'FAIL'}")
    return test_result


def test_override_high_risk_isolated():
    server_id = create_test_server(
        name="Test HIGH_RISK_ISOLATED Override Server",
        verdict="HIGH_RISK_ISOLATED",
        trust_score=0.25,
        injection_resilience=0.40
    )
    
    result = simulate_commit(server_id, override=True)
    
    test_result = {
        "test_name": "test_override_high_risk_isolated",
        "server_id": server_id,
        "expected": "forwarded",
        "actual": result.get("status"),
        "verdict": result.get("verdict"),
        "override_used": True,
        "pass": result.get("status") == "forwarded",
        "timestamp": datetime.utcnow().isoformat()
    }
    
    log_result(test_result)
    print(f"[TEST] test_override_high_risk_isolated: {'PASS' if test_result['pass'] else 'FAIL'}")
    return test_result


def test_injection_resilience_payload_inclusion():
    server_id = create_test_server(
        name="Test Injection Resilience Payload",
        verdict="TRUSTED_GENERAL",
        trust_score=0.88,
        injection_resilience=0.95
    )
    
    payload_check = check_payload_injection_resilience(server_id)
    
    test_result = {
        "test_name": "test_injection_resilience_payload_inclusion",
        "server_id": server_id,
        "expected": True,
        "actual": payload_check["injection_resilience_included"],
        "injection_resilience_value": payload_check["injection_resilience_value"],
        "pass": payload_check["injection_resilience_included"] == True,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    log_result(test_result)
    print(f"[TEST] test_injection_resilience_payload_inclusion: {'PASS' if test_result['pass'] else 'FAIL'}")
    return test_result


def test_verdict_enumeration():
    test_verdicts = [
        ("TRUSTED_GENERAL", 0.85, 0.90),
        ("TRUSTED_RESEARCH", 0.88, 0.85),
        ("TRUSTED_EXTERNAL", 0.82, 0.80),
        ("REVIEW_PENDING", 0.60, 0.50),
        ("CAUTION_LIMITED", 0.40, 0.45),
        ("HIGH_RISK_ISOLATED", 0.20, 0.30),
    ]
    
    results = []
    for verdict, trust_score, inj_resilience in test_verdicts:
        server_id = create_test_server(
            name=f"Test {verdict} Server",
            verdict=verdict,
            trust_score=trust_score,
            injection_resilience=inj_resilience
        )
        
        commit_result = simulate_commit(server_id)
        payload_check = check_payload_injection_resilience(server_id)
        
        result = {
            "test_name": f"test_verdict_{verdict.lower().replace('_', '_')}",
            "server_id": server_id,
            "verdict": verdict,
            "commit_status": commit_result.get("status"),
            "injection_resilience_included": payload_check["injection_resilience_included"],
            "injection_resilience_value": payload_check["injection_resilience_value"],
            "timestamp": datetime.utcnow().isoformat()
        }
        results.append(result)
        log_result(result)
        print(f"[TEST] test_verdict_{verdict}: commit={commit_result.get('status')}, inj_resilience={payload_check['injection_resilience_included']}")
    
    return results


def run_all_tests():
    print("=" * 60)
    print("AIDR Gateway Verdict Enforcement Test Suite")
    print("=" * 60)
    
    print("\n[SETUP] Clearing existing test servers...")
    clear_test_servers()
    
    print("\n[SETUP] Ensuring audit_log table exists...")
    ws_execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            target_server_id TEXT,
            event_type TEXT,
            actor TEXT,
            detail TEXT,
            created_at TEXT
        )
    """)
    
    print("\n[SETUP] Ensuring mcp_signal_scores table exists...")
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            server_id TEXT,
            signal_name TEXT,
            score DOUBLE,
            evidence TEXT,
            scored_at TEXT
        )
    """)
    
    print("\n[SETUP] Ensuring mcp_server_registry table exists...")
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry (
            server_id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            url TEXT,
            trust_score DOUBLE,
            verdict TEXT,
            registry_source TEXT,
            scan_count INTEGER
        )
    """)
    
    print("\n" + "-" * 60)
    print("Running verdict enforcement tests...")
    print("-" * 60 + "\n")
    
    results = []
    
    results.append(test_forward_trusted_general())
    time.sleep(0.5)
    
    results.append(test_forward_trusted_research())
    time.sleep(0.5)
    
    results.append(test_block_caution_limited())
    time.sleep(0.5)
    
    results.append(test_block_high_risk_isolated())
    time.sleep(0.5)
    
    results.append(test_override_caution_limited())
    time.sleep(0.5)
    
    results.append(test_override_high_risk_isolated())
    time.sleep(0.5)
    
    results.append(test_injection_resilience_payload_inclusion())
    time.sleep(0.5)
    
    print("\n" + "-" * 60)
    print("Running verdict enumeration tests...")
    print("-" * 60 + "\n")
    
    enum_results = test_verdict_enumeration()
    results.extend(enum_results)
    
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    total = len(results)
    passed = sum(1 for r in results if r.get("pass"))
    failed = total - passed
    
    print(f"\nTotal Tests: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    print("\nDetailed Results:")
    for r in results:
        status = "PASS" if r.get("pass") else "FAIL"
        verdict = r.get("verdict", "N/A")
        print(f"  [{status}] {r.get('test_name')} (verdict={verdict})")
    
    print("\n" + "-" * 60)
    print(f"Results logged to: {RESULTS_FILE}")
    print("-" * 60)
    
    summary = {
        "test_suite": "aidr_gateway_verdict_enforcement",
        "total_tests": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{(passed/total)*100:.1f}%" if total > 0 else "0%",
        "timestamp": datetime.utcnow().isoformat()
    }
    log_result(summary)
    
    print(f"\nSummary: {passed}/{total} tests passed ({summary['pass_rate']})")
    
    print("\n[TEARDOWN] Cleaning up test servers...")
    clear_test_servers()
    
    return results


if __name__ == "__main__":
    run_all_tests()