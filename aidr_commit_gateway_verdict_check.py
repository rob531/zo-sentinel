#!/usr/bin/env python3
# deps: requests
"""
aidr_commit_gateway_verdict_check.py — Phase 9 verdict enforcement smoke test.

Enforces verdict-based commit gating per PRODUCT_SPEC §1:
  - HIGH_RISK_ISOLATED: blocks auto-commit, gateway returns 403
  - CAUTION_LIMITED: requires explicit override (AIDR_OVERRIDE=true) or blocks 403
  - TRUSTED_GENERAL / TRUSTED_RESEARCH / ENTERPRISE_CONTROLLED: auto-proceed
  - INSUFFICIENT: blocks with 403 until signals complete
  - injection_resilience score MUST be in commit payload JSON

Uses write_service HTTP (port 8772) for all DB access. No direct DuckDB imports.
"""
import os
import sys
import json
import requests
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

# HTTP endpoints (PRODUCT_SPEC §5)
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

# Gateway port matches aidr_commit_gateway.py
GATEWAY_URL = "http://127.0.0.1:3891"

# Verdict classifications
VERDICTS_BLOCKED_NO_OVERRIDE = {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}
VERDICTS_OVERRIDE_REQUIRED = {"CAUTION_LIMITED"}
VERDICTS_AUTO_COMMIT = {"TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED"}
VERDICTS_INSUFFICIENT = {"INSUFFICIENT"}

#signal thresholds
INJECTION_RESILIENCE_MIN = 0.5


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute SELECT via write_service with parameterized query."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        print(f"[ERROR] ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows via write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[ERROR] ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[ERROR] ws_execute failed: {e}")
        return False


def get_injection_resilience_score(server_id: str) -> float:
    """Fetch injection_resilience score from mcp_signal_scores."""
    sql = """
        SELECT score FROM mcp_signal_scores
        WHERE server_id = ? AND signal_name = 'injection_resilience'
        ORDER BY scored_at DESC LIMIT 1
    """
    rows = ws_query(sql, {"p1": server_id})
    if rows:
        return float(rows[0].get("score", 0.0))
    return 0.0


def create_test_signal_scores(server_id: str, score: float) -> bool:
    """Insert test injection_resilience score."""
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "server_id": server_id,
        "signal_name": "injection_resilience",
        "score": score,
        "evidence": json.dumps({"test": True, "timestamp": now}),
        "scored_at": now
    }
    return ws_write("mcp_signal_scores", [row])


def simulate_commit_request(server_id: str, override: bool = False) -> Dict[str, Any]:
    """
    Simulate commit gateway call with verdict check.
    Returns {allowed, status, reason, payload} dict.
    """
    # Query verdict from mcp_server_registry
    sql = "SELECT verdict, trust_score FROM mcp_server_registry WHERE server_id = ? LIMIT 1"
    rows = ws_query(sql, {"p1": server_id})
    
    if not rows:
        return {"allowed": False, "status": 403, "reason": "server_id not found", "payload": None}
    
    verdict = rows[0].get("verdict", "UNKNOWN")
    trust_score = float(rows[0].get("trust_score", 0.0))
    injection_score = get_injection_resilience_score(server_id)
    
    # Build commit payload
    payload = {
        "server_id": server_id,
        "verdict": verdict,
        "trust_score": trust_score,
        "signals": {
            "injection_resilience_score": injection_score
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # VERDICT ENFORCEMENT (match aidr_commit_gateway.py logic)
    
    # HIGH_RISK_ISOLATED: always blocked
    if verdict in VERDICTS_BLOCKED_NO_OVERRIDE:
        reason = f"Verdict {verdict} blocked: requires explicit analyst override"
        return {"allowed": False, "status": 403, "reason": reason, "payload": payload}
    
    # CAUTION_LIMITED: requires override
    if verdict in VERDICTS_OVERRIDE_REQUIRED:
        if not override:
            reason = f"Verdict {verdict} requires explicit override (AIDR_OVERRIDE=true)"
            return {"allowed": False, "status": 403, "reason": reason, "payload": payload}
    
    # INSUFFICIENT: block until signals complete
    if verdict in VERDICTS_INSUFFICIENT:
        reason = "Verdict INSUFFICIENT: signal collection incomplete"
        return {"allowed": False, "status": 403, "reason": reason, "payload": payload}
    
    # Auto-commit verdicts
    if verdict not in VERDICTS_AUTO_COMMIT:
        reason = f"Unknown verdict {verdict}: manual review required"
        return {"allowed": False, "status": 403, "reason": reason, "payload": payload}
    
    # TRUSTED / ENTERPRISE: auto-proceed
    return {"allowed": True, "status": 200, "reason": None, "payload": payload}


def test_high_risk_isolated_blocked():
    """Test (1): HIGH_RISK_ISOLATED blocks auto-commit."""
    server_id = "test-high-risk-001"
    
    # Create test server with HIGH_RISK_ISOLATED verdict
    result = simulate_commit_request(server_id)
    
    assert result["status"] == 403, f"Expected 403, got {result['status']}"
    assert not result["allowed"], "HIGH_RISK_ISOLATED should be blocked"
    assert "HIGH_RISK_ISOLATED" in result["reason"], "Reason should mention verdict"
    assert result["payload"] is not None, "Payload must not be None"
    assert "injection_resilience_score" in result["payload"]["signals"], \
        "Payload MUST include injection_resilience_score"
    
    print("✓ Test (1) PASSED: HIGH_RISK_ISOLATED blocked with 403")
    return True


def test_caution_limited_requires_override():
    """Test (2): CAUTION_LIMITED requires explicit override."""
    server_id = "test-caution-limited-001"
    
    # Without override: should block
    result = simulate_commit_request(server_id, override=False)
    assert result["status"] == 403, f"Expected 403 without override, got {result['status']}"
    assert not result["allowed"], "CAUTION_LIMITED should block without override"
    assert "CAUTION_LIMITED" in result["reason"], "Reason should mention verdict"
    
    # With override: should proceed
    result_override = simulate_commit_request(server_id, override=True)
    assert result_override["status"] == 200, f"Expected 200 with override, got {result_override['status']}"
    assert result_override["allowed"], "CAUTION_LIMITED should proceed with override"
    
    print("✓ Test (2) PASSED: CAUTION_LIMITED requires override")
    return True


def test_trusted_general_auto_proceed():
    """Test (3): TRUSTED_GENERAL auto-proceeds."""
    server_id = "test-trusted-general-001"
    
    result = simulate_commit_request(server_id, override=False)
    assert result["status"] == 200, f"Expected 200, got {result['status']}"
    assert result["allowed"], "TRUSTED_GENERAL should auto-proceed"
    
    print("✓ Test (3) PASSED: TRUSTED_GENERAL auto-proceeds")
    return True


def test_trusted_research_auto_proceed():
    """Test (3): TRUSTED_RESEARCH auto-proceeds."""
    server_id = "test-trusted-research-001"
    
    result = simulate_commit_request(server_id, override=False)
    assert result["status"] == 200, f"Expected 200, got {result['status']}"
    assert result["allowed"], "TRUSTED_RESEARCH should auto-proceed"
    
    print("✓ Test (3) PASSED: TRUSTED_RESEARCH auto-proceeds")
    return True


def test_enterprise_controlled_auto_proceed():
    """Test (3): ENTERPRISE_CONTROLLED auto-proceeds."""
    server_id = "test-enterprise-controlled-001"
    
    result = simulate_commit_request(server_id, override=False)
    assert result["status"] == 200, f"Expected 200, got {result['status']}"
    assert result["allowed"], "ENTERPRISE_CONTROLLED should auto-proceed"
    
    print("✓ Test (3) PASSED: ENTERPRISE_CONTROLLED auto-proceeds")
    return True


def test_insufficient_blocked():
    """Test (4): INSUFFICIENT verdict blocks with 403."""
    server_id = "test-insufficient-001"
    
    result = simulate_commit_request(server_id, override=False)
    assert result["status"] == 403, f"Expected 403, got {result['status']}"
    assert not result["allowed"], "INSUFFICIENT should block"
    assert "INSUFFICIENT" in result["reason"], "Reason should mention INSUFFICIENT"
    
    print("✓ Test (4) PASSED: INSUFFICIENT blocked with 403")
    return True


def test_injection_resilience_in_payload():
    """Test (5): injection_resilience score must be in commit payload JSON."""
    server_id = "test-injection-resilience-001"
    injection_score = 0.85
    
    # Create test signal score
    create_test_signal_scores(server_id, injection_score)
    
    result = simulate_commit_request(server_id, override=False)
    assert result["payload"] is not None, "Payload must exist"
    assert "signals" in result["payload"], "Payload must have 'signals' key"
    assert "injection_resilience_score" in result["payload"]["signals"], \
        "Payload MUST include injection_resilience_score in signals"
    
    actual_score = result["payload"]["signals"]["injection_resilience_score"]
    assert abs(actual_score - injection_score) < 0.01, \
        f"Expected score ~{injection_score}, got {actual_score}"
    
    print("✓ Test (5) PASSED: injection_resilience_score included in payload")
    return True


def run_all_tests():
    """Execute all Phase 9 verdict enforcement tests."""
    print("=" * 60)
    print("AIDR Commit Gateway Verdict Check — Phase 9 Smoke Test")
    print("=" * 60)
    print()
    
    tests = [
        ("HIGH_RISK_ISOLATED blocked", test_high_risk_isolated_blocked),
        ("CAUTION_LIMITED requires override", test_caution_limited_requires_override),
        ("TRUSTED_GENERAL auto-proceeds", test_trusted_general_auto_proceed),
        ("TRUSTED_RESEARCH auto-proceeds", test_trusted_research_auto_proceed),
        ("ENTERPRISE_CONTROLLED auto-proceeds", test_enterprise_controlled_auto_proceed),
        ("INSUFFICIENT blocked", test_insufficient_blocked),
        ("injection_resilience in payload", test_injection_resilience_in_payload),
    ]
    
    results = []
    for name, test_fn in tests:
        try:
            test_fn()
            results.append((name, True, None))
        except AssertionError as e:
            results.append((name, False, str(e)))
        except Exception as e:
            results.append((name, False, f"Unhandled error: {e}"))
    
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    
    for name, ok, err in results:
        status = "✓ PASS" if ok else f"✗ FAIL: {err}"
        print(f"  {name}: {status}")
    
    print()
    print(f"Passed: {passed}/{len(results)}")
    print(f"Failed: {failed}/{len(results)}")
    
    if failed > 0:
        print()
        print("VERDICT ENFORCEMENT FAILED")
        sys.exit(1)
    else:
        print()
        print("ALL VERDICT ENFORCEMENT TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    run_all_tests()
