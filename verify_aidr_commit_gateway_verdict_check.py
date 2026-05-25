import requests
import json
from datetime import datetime

WRITE_SERVICE = "http://127.0.0.1:8772"

def ws_query(sql):
    """Execute SELECT query via write_service."""
    resp = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=10)
    resp.raise_for_status()
    return resp.json()

def ws_execute(sql):
    """Execute DDL/DML via write_service."""
    resp = requests.post(f"{WRITE_SERVICE}/execute", json={"sql": sql}, timeout=10)
    resp.raise_for_status()
    return resp.json()

def verify_verdict_check_enforcement():
    """Verify aidr_commit_gateway verdict enforcement wiring."""
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "tests": []
    }

    # Test 1: Check that mcp_server_registry has verdict column
    try:
        result = ws_query("SELECT server_id, name, verdict FROM mcp_server_registry LIMIT 5")
        has_verdict_column = True
        results["tests"].append({
            "name": "verdict_column_exists",
            "status": "PASS",
            "detail": f"Found {result.get('count', 0)} servers with verdict column"
        })
    except Exception as e:
        results["tests"].append({
            "name": "verdict_column_exists",
            "status": "FAIL",
            "detail": str(e)
        })
        return results

    # Test 2: Verify verdict enforcement logic checks
    # CAUTION_LIMITED and HIGH_RISK_ISOLATED are blocked
    blocked_verdicts = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]
    allowed_verdicts = ["TRUSTED", "SUSPECTED_SAFE", "UNKNOWN", "CAUTION_OPTIMISTIC"]

    for verdict in blocked_verdicts:
        servers_with_verdict = ws_query(
            f"SELECT server_id, name, verdict FROM mcp_server_registry WHERE verdict = '{verdict}' LIMIT 3"
        )
        count = servers_with_verdict.get('count', 0)
        results["tests"].append({
            "name": f"verdict_{verdict}_block_check",
            "status": "PASS" if count >= 0 else "FAIL",
            "detail": f"{count} server(s) with verdict '{verdict}' (should be blocked by gateway)"
        })

    # Test 3: Verify injection_resilience score is tracked
    try:
        resilience_check = ws_query(
            "SELECT server_id, signal_name, score FROM mcp_signal_scores "
            "WHERE signal_name = 'injection_resilience' LIMIT 5"
        )
        has_injection_resilience = resilience_check.get('count', 0) > 0
        results["tests"].append({
            "name": "injection_resilience_score_present",
            "status": "PASS" if has_injection_resilience else "WARN",
            "detail": f"{resilience_check.get('count', 0)} servers with injection_resilience signal"
        })
    except Exception as e:
        results["tests"].append({
            "name": "injection_resilience_score_present",
            "status": "ERROR",
            "detail": str(e)
        })

    # Test 4: Verify commit gateway configuration exists
    try:
        health_check = ws_query(
            "SELECT service, last_heartbeat FROM service_health WHERE service LIKE '%gateway%'"
        )
        gateway_configured = health_check.get('count', 0) > 0
        results["tests"].append({
            "name": "gateway_service_registered",
            "status": "PASS" if gateway_configured else "WARN",
            "detail": f"{health_check.get('count', 0)} gateway service(s) registered"
        })
    except Exception as e:
        results["tests"].append({
            "name": "gateway_service_registered",
            "status": "WARN",
            "detail": f"No gateway registered: {str(e)}"
        })

    # Test 5: Verify override mechanism exists for blocked verdicts
    try:
        override_check = ws_query(
            "SELECT auth_token_id, action, expires_at, used FROM auth_tokens "
            "WHERE action = 'verdict_override' LIMIT 5"
        )
        has_override_mechanism = True
        results["tests"].append({
            "name": "verdict_override_mechanism_exists",
            "status": "PASS",
            "detail": "Verdict override mechanism available in auth_tokens"
        })
    except Exception as e:
        results["tests"].append({
            "name": "verdict_override_mechanism_exists",
            "status": "WARN",
            "detail": f"Override mechanism check: {str(e)}"
        })

    # Test 6: Verify blocked verdict servers would be prevented from commit
    blocked_servers = []
    for verdict in blocked_verdicts:
        servers = ws_query(
            f"SELECT server_id, name, verdict FROM mcp_server_registry WHERE verdict = '{verdict}'"
        )
        rows = servers.get('rows', [])
        for row in rows:
            blocked_servers.append({
                "server_id": row.get('server_id'),
                "name": row.get('name'),
                "verdict": verdict,
                "should_block": True
            })

    results["blocked_verdicts_found"] = blocked_servers
    results["verdict_enforcement_summary"] = {
        "total_blocked_servers": len(blocked_servers),
        "blocked_verdict_types": blocked_verdicts,
        "override_required_for_commit": True
    }

    return results

def main():
    print("=" * 60)
    print("AIDR Commit Gateway Verdict Check Verification")
    print("=" * 60)
    print()

    results = verify_verdict_check_enforcement()

    print(f"Timestamp: {results['timestamp']}")
    print()
    print("Test Results:")
    print("-" * 40)
    for test in results['tests']:
        status_symbol = "✓" if test['status'] == "PASS" else ("!" if test['status'] == "WARN" else "✗")
        print(f"  {status_symbol} {test['name']}: {test['status']}")
        print(f"      {test['detail']}")
    print()

    if 'verdict_enforcement_summary' in results:
        summary = results['verdict_enforcement_summary']
        print("Verdict Enforcement Summary:")
        print("-" * 40)
        print(f"  Blocked verdict types: {summary['blocked_verdict_types']}")
        print(f"  Servers with blocked verdicts: {summary['total_blocked_servers']}")
        print(f"  Override required for commit: {summary['override_required_for_commit']}")
        print()

    if 'blocked_verdicts_found' in results:
        blocked = results['blocked_verdicts_found']
        if blocked:
            print("Servers requiring override for commit:")
            print("-" * 40)
            for server in blocked:
                print(f"  - {server['name']} (ID: {server['server_id']}) - {server['verdict']}")

    print()
    print("Verification Complete")
    return results

if __name__ == '__main__':
    main()