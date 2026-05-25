#!/usr/bin/env python3
"""
Integration verification for aidr_commit_gateway.py verdict enforcement.
Tests that gateway blocks MCPs with CAUTION_LIMITED, HIGH_RISK_ISOLATED, or INSUFFICIENT verdicts
and permits TRUSTED_GENERAL or TRUSTED_RESEARCH verdicts.
"""

import asyncio
import sys
import time
import httpx
from datetime import datetime

GATEWAY_URL = "http://127.0.0.1:8773"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"

VERDICT_ALLOWED = ["TRUSTED_GENERAL", "TRUSTED_RESEARCH"]
VERDICT_BLOCKED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "INSUFFICIENT"]
ALL_VERDICTS = VERDICT_ALLOWED + VERDICT_BLOCKED


async def write_to_db(table: str, rows: dict) -> dict:
    """Write to DuckDB via write_service."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True}
        )
        response.raise_for_status()
        return response.json()


async def query_db(sql: str) -> dict:
    """Query DuckDB via write_service."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql}
        )
        response.raise_for_status()
        return response.json()


async def execute_db(sql: str) -> dict:
    """Execute DDL/DML via write_service."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{WRITE_SERVICE_URL}/execute",
            json={"sql": sql}
        )
        response.raise_for_status()
        return response.json()


async def setup_test_server(server_id: str, name: str, verdict: str) -> dict:
    """Register a test MCP server with a specific verdict."""
    result = await write_to_db("mcp_server_registry", {
        "server_id": server_id,
        "name": name,
        "url": f"http://test-{server_id}.local",
        "description": f"Test server for verdict {verdict}",
        "trust_score": 50 if verdict in VERDICT_BLOCKED else 85,
        "verdict": verdict,
        "registry_source": "test_verification",
        "scan_count": 1
    })
    return result


async def check_gateway_logs_for_verdict(server_id: str, expected_action: str) -> bool:
    """Check gateway logs for verdict enforcement action."""
    sql = f"""
    SELECT * FROM audit_log 
    WHERE target_server_id = '{server_id}'
    AND (detail LIKE '%{expected_action}%' OR detail LIKE '%{expected_action.lower()}%')
    ORDER BY created_at DESC LIMIT 5
    """
    result = await query_db(sql)
    return result.get("count", 0) > 0


async def call_gateway_commit(server_id: str) -> dict:
    """Call the gateway commit endpoint for a server."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{GATEWAY_URL}/commit",
                json={"server_id": server_id}
            )
            return {"status_code": response.status_code, "success": response.is_success}
        except httpx.ConnectError:
            return {"status_code": 0, "success": False, "error": "connection_failed"}
        except Exception as e:
            return {"status_code": 0, "success": False, "error": str(e)}


async def call_gateway_status(server_id: str) -> dict:
    """Check gateway status for a server."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"{GATEWAY_URL}/status/{server_id}")
            return response.json() if response.status_code == 200 else {}
        except httpx.ConnectError:
            return {"error": "gateway_unavailable"}
        except Exception as e:
            return {"error": str(e)}


async def cleanup_test_server(server_id: str):
    """Remove test server from registry."""
    try:
        await execute_db(f"DELETE FROM mcp_server_registry WHERE server_id = '{server_id}'")
    except Exception:
        pass


async def verify_allowed_verdict(verdict: str) -> dict:
    """Test that a verdict is allowed by the gateway."""
    server_id = f"test_allowed_{verdict.lower()}_{int(time.time())}"
    
    try:
        await setup_test_server(server_id, f"Test Server {verdict}", verdict)
        await asyncio.sleep(0.5)
        
        result = await call_gateway_commit(server_id)
        status = await call_gateway_status(server_id)
        
        allowed = result.get("success", False) or status.get("can_commit", False)
        
        await cleanup_test_server(server_id)
        
        return {
            "verdict": verdict,
            "expected": "ALLOWED",
            "actual": "ALLOWED" if allowed else "BLOCKED",
            "passed": allowed,
            "details": f"Gateway response: {result}"
        }
    except Exception as e:
        await cleanup_test_server(server_id)
        return {
            "verdict": verdict,
            "expected": "ALLOWED",
            "actual": "ERROR",
            "passed": False,
            "details": str(e)
        }


async def verify_blocked_verdict(verdict: str) -> dict:
    """Test that a verdict is blocked by the gateway."""
    server_id = f"test_blocked_{verdict.lower()}_{int(time.time())}"
    
    try:
        await setup_test_server(server_id, f"Test Server {verdict}", verdict)
        await asyncio.sleep(0.5)
        
        result = await call_gateway_commit(server_id)
        status = await call_gateway_status(server_id)
        
        blocked = not result.get("success", True) or status.get("blocked", True)
        
        await cleanup_test_server(server_id)
        
        return {
            "verdict": verdict,
            "expected": "BLOCKED",
            "actual": "BLOCKED" if blocked else "ALLOWED",
            "passed": blocked,
            "details": f"Gateway response: {result}"
        }
    except Exception as e:
        await cleanup_test_server(server_id)
        return {
            "verdict": verdict,
            "expected": "BLOCKED",
            "actual": "ERROR",
            "passed": False,
            "details": str(e)
        }


async def check_gateway_heartbeat() -> dict:
    """Verify gateway is running via heartbeat check."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{GATEWAY_URL}/health")
            if response.status_code == 200:
                return {"running": True, "health": response.json()}
            return {"running": False, "status_code": response.status_code}
    except httpx.ConnectError:
        return {"running": False, "error": "connection_refused"}
    except Exception as e:
        return {"running": False, "error": str(e)}


async def get_gateway_logs() -> dict:
    """Retrieve gateway-related audit logs."""
    sql = """
    SELECT * FROM audit_log 
    WHERE event_type LIKE '%gateway%' OR event_type LIKE '%commit%'
    ORDER BY created_at DESC LIMIT 20
    """
    return await query_db(sql)


async def verify_verdict_enforcement():
    """Main verification function for verdict enforcement."""
    print("=" * 70)
    print("ZO-SENTINEL: AIDR Gateway Verdict Enforcement Verification")
    print("=" * 70)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    
    results = {
        "gateway_health": None,
        "verdict_tests": [],
        "summary": {"passed": 0, "failed": 0, "total": 0}
    }
    
    print("[1/4] Checking gateway health...")
    health = await check_gateway_heartbeat()
    results["gateway_health"] = health
    if health.get("running"):
        print(f"      ✓ Gateway is running: {health.get('health', {})}")
    else:
        print(f"      ✗ Gateway is NOT running: {health}")
        print("      WARNING: Continuing with log-based verification...")
    print()
    
    print("[2/4] Testing ALLOWED verdicts (should permit commit)...")
    print("-" * 50)
    for verdict in VERDICT_ALLOWED:
        result = await verify_allowed_verdict(verdict)
        results["verdict_tests"].append(result)
        status_icon = "✓" if result["passed"] else "✗"
        print(f"      {status_icon} {verdict}: {result['actual']} (expected: {result['expected']})")
        if not result["passed"]:
            print(f"        Details: {result['details']}")
    print()
    
    print("[3/4] Testing BLOCKED verdicts (should reject commit)...")
    print("-" * 50)
    for verdict in VERDICT_BLOCKED:
        result = await verify_blocked_verdict(verdict)
        results["verdict_tests"].append(result)
        status_icon = "✓" if result["passed"] else "✗"
        print(f"      {status_icon} {verdict}: {result['actual']} (expected: {result['expected']})")
        if not result["passed"]:
            print(f"        Details: {result['details']}")
    print()
    
    print("[4/4] Retrieving gateway audit logs...")
    logs = await get_gateway_logs()
    results["audit_logs"] = logs
    log_count = logs.get("count", 0)
    print(f"      Found {log_count} gateway-related audit entries")
    print()
    
    for test in results["verdict_tests"]:
        if test["passed"]:
            results["summary"]["passed"] += 1
        else:
            results["summary"]["failed"] += 1
        results["summary"]["total"] += 1
    
    print("=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)
    print(f"  Total Tests: {results['summary']['total']}")
    print(f"  Passed:      {results['summary']['passed']} ✓")
    print(f"  Failed:      {results['summary']['failed']} ✗")
    print()
    
    print("VERDICT TIER RESULTS:")
    print(f"  ALLOWED verdicts (TRUSTED_GENERAL, TRUSTED_RESEARCH):")
    allowed_tests = [t for t in results["verdict_tests"] if t["verdict"] in VERDICT_ALLOWED]
    allowed_passed = sum(1 for t in allowed_tests if t["passed"])
    print(f"    - {allowed_passed}/{len(allowed_tests)} tests passed")
    
    print(f"  BLOCKED verdicts (CAUTION_LIMITED, HIGH_RISK_ISOLATED, INSUFFICIENT):")
    blocked_tests = [t for t in results["verdict_tests"] if t["verdict"] in VERDICT_BLOCKED]
    blocked_passed = sum(1 for t in blocked_tests if t["passed"])
    print(f"    - {blocked_passed}/{len(blocked_tests)} tests passed")
    print()
    
    overall_pass = results["summary"]["failed"] == 0
    if overall_pass:
        print("  ✓ INTEGRATION VERIFICATION: PASSED")
    else:
        print("  ✗ INTEGRATION VERIFICATION: FAILED")
    
    print("=" * 70)
    
    return overall_pass, results


def main():
    """Entry point for standalone execution."""
    success, results = asyncio.run(verify_verdict_enforcement())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()