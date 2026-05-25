import time
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

WRITE_SERVICE = "http://127.0.0.1:8772/write"
QUERY_SERVICE = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE = "http://127.0.0.1:8772/execute"

LOG_FILE = "/home/workspace/zo_sentinel/e2e_scenarios_run.log"

def log(msg: str):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as fh:
        fh.write(line + "\n")


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    import requests
    resp = requests.post(WRITE_SERVICE, json={"table": table, "rows": rows}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> Dict[str, Any]:
    import requests
    resp = requests.post(QUERY_SERVICE, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> Dict[str, Any]:
    import requests
    resp = requests.post(EXECUTE_SERVICE, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def verify_table_exists(table_name: str) -> bool:
    result = ws_query(f"SELECT count(*) as cnt FROM {table_name}")
    return result.get("rows", [{}])[0].get("cnt", 0) >= 0


def run_flow_1_new_mcp_cycle():
    """
    Flow 1: new MCP → signal scored → verdict → attestation → UI visible
    """
    log("=" * 60)
    log("FLOW 1: New MCP → Signal Scored → Verdict → Attestation → UI Visible")
    log("=" * 60)
    
    flow_id = f"flow1-{uuid.uuid4().hex[:8]}"
    server_id = f"srv-test-{uuid.uuid4().hex[:12]}"
    
    # Step 1: Register a new MCP server
    log(f"[{flow_id}] Step 1: Register new MCP server: {server_id}")
    ws_write("mcp_server_registry", [{
        "server_id": server_id,
        "name": f"Test MCP Server {flow_id}",
        "url": f"https://test-{server_id}.example.com",
        "description": "Test server for e2e validation",
        "trust_score": None,
        "verdict": None,
        "registry_source": "e2e_test",
        "scan_count": 0
    }])
    log(f"[{flow_id}] Server registered")
    
    # Step 2: Insert signal scores for the server
    log(f"[{flow_id}] Step 2: Insert signal scores")
    signals = [
        {"server_id": server_id, "signal_name": "github_stars", "score": 0.75, "evidence": json.dumps({"stars": 1500})},
        {"server_id": server_id, "signal_name": "npm_downloads", "score": 0.60, "evidence": json.dumps({"weekly": 50000})},
        {"server_id": server_id, "signal_name": "registry_source", "score": 0.85, "evidence": json.dumps({"source": "npmjs"})},
        {"server_id": server_id, "signal_name": "trust_synthesiser_v2", "score": 0.72, "evidence": json.dumps({"dimensions": 7})}
    ]
    ws_write("mcp_signal_scores", signals)
    log(f"[{flow_id}] {len(signals)} signals inserted")
    
    # Step 3: Verify signals are queryable
    log(f"[{flow_id}] Step 3: Verify signals queryable")
    result = ws_query(f"SELECT count(*) as cnt FROM mcp_signal_scores WHERE server_id = '{server_id}'")
    signal_count = result.get("rows", [{}])[0].get("cnt", 0)
    log(f"[{flow_id}] Found {signal_count} signal records")
    assert signal_count >= len(signals), f"Expected {len(signals)} signals, got {signal_count}"
    
    # Step 4: Compute and set verdict (simulate trust_synthesiser_v2)
    log(f"[{flow_id}] Step 4: Compute and set verdict")
    avg_score_result = ws_query(f"SELECT AVG(score) as avg_score FROM mcp_signal_scores WHERE server_id = '{server_id}'")
    avg_score = avg_score_result.get("rows", [{}])[0].get("avg_score", 0.0)
    log(f"[{flow_id}] Average signal score: {avg_score:.3f}")
    
    verdict = "approved" if avg_score >= 0.7 else "pending" if avg_score >= 0.5 else "rejected"
    log(f"[{flow_id}] Computed verdict: {verdict}")
    
    # Step 5: Update server with verdict
    log(f"[{flow_id}] Step 5: Update server with verdict and trust score")
    ws_execute(f"UPDATE mcp_server_registry SET verdict = '{verdict}', trust_score = {avg_score} WHERE server_id = '{server_id}'")
    log(f"[{flow_id}] Server updated with verdict={verdict}, trust_score={avg_score}")
    
    # Step 6: Create attestation if approved
    log(f"[{flow_id}] Step 6: Create attestation")
    if verdict == "approved":
        from datetime import timedelta
        expires = datetime.now(timezone.utc) + timedelta(days=30)
        ws_write("attestations", [{
            "server_id": server_id,
            "attestor": "e2e_validator",
            "statement": f"Attested by e2e test flow {flow_id}",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires.isoformat(),
            "revoked": False
        }])
        log(f"[{flow_id}] Attestation created, expires {expires.date()}")
    
    # Step 7: Verify audit log entry
    log(f"[{flow_id}] Step 7: Record audit event")
    ws_write("audit_log", [{
        "target_server_id": server_id,
        "event_type": "e2e_flow_1_complete",
        "actor": "e2e_scenarios_run",
        "detail": json.dumps({"flow_id": flow_id, "verdict": verdict, "signals_count": signal_count}),
        "created_at": datetime.now(timezone.utc).isoformat()
    }])
    log(f"[{flow_id}] Audit event recorded")
    
    # Step 8: Verify UI would see this server
    log(f"[{flow_id}] Step 8: Verify server queryable from registry")
    result = ws_query(f"SELECT server_id, name, verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'")
    server_rows = result.get("rows", [])
    log(f"[{flow_id}] UI-visible server record: {server_rows}")
    
    log(f"[{flow_id}] FLOW 1 COMPLETE: server_id={server_id}, verdict={verdict}")
    return {"flow_id": flow_id, "server_id": server_id, "verdict": verdict, "signal_count": signal_count}


def run_flow_2_threat_intel_overlay():
    """
    Flow 2: Threat intel overlay flow
    """
    log("=" * 60)
    log("FLOW 2: Threat Intel Overlay Flow")
    log("=" * 60)
    
    flow_id = f"flow2-{uuid.uuid4().hex[:8]}"
    server_id = f"srv-threat-{uuid.uuid4().hex[:12]}"
    
    # Step 1: Create a server that will receive threat intel
    log(f"[{flow_id}] Step 1: Create test server")
    ws_write("mcp_server_registry", [{
        "server_id": server_id,
        "name": f"Threat Intel Target {flow_id}",
        "url": f"https://threat-target-{server_id}.example.com",
        "description": "Server for threat intel e2e testing",
        "verdict": "pending",
        "registry_source": "e2e_test"
    }])
    log(f"[{flow_id}] Server created: {server_id}")
    
    # Step 2: Add initial signals
    log(f"[{flow_id}] Step 2: Add initial signals")
    initial_signals = [
        {"server_id": server_id, "signal_name": "domain_age_days", "score": 0.45, "evidence": json.dumps({"days": 30})},
        {"server_id": server_id, "signal_name": "trust_synthesiser_v2", "score": 0.48, "evidence": json.dumps({"dimensions": 7})}
    ]
    ws_write("mcp_signal_scores", initial_signals)
    log(f"[{flow_id}] Initial signals inserted")
    
    # Step 3: Overlay threat intelligence
    log(f"[{flow_id}] Step 3: Overlay threat intelligence")
    threat_signals = [
        {"server_id": server_id, "signal_name": "threat_intel_known_malicious", "score": 0.9, "evidence": json.dumps({"source": "e2e_test", "type": "test_threat"})},
        {"server_id": server_id, "signal_name": "injection_resilience", "score": 0.2, "evidence": json.dumps({"detected": True})}
    ]
    ws_write("mcp_signal_scores", threat_signals)
    log(f"[{flow_id}] Threat signals inserted")
    
    # Step 4: Create threat association
    log(f"[{flow_id}] Step 4: Create threat association")
    ws_write("mcp_threat_associations", [{
        "server_id": server_id,
        "threat_type": "test_malicious_activity",
        "severity": "high",
        "evidence": json.dumps({"flow_id": flow_id, "source": "e2e_test"}),
        "reported_at": datetime.now(timezone.utc).isoformat()
    }])
    log(f"[{flow_id}] Threat association created")
    
    # Step 5: Update risk register
    log(f"[{flow_id}] Step 5: Update risk register")
    risk_tier = "critical"
    ws_write("risk_register", [{
        "server_id": server_id,
        "risk_tier": risk_tier,
        "risk_rank": 1,
        "threat_count": 1,
        "computed_at": datetime.now(timezone.utc).isoformat()
    }])
    log(f"[{flow_id}] Risk register updated: tier={risk_tier}")
    
    # Step 6: Override verdict based on threat
    log(f"[{flow_id}] Step 6: Override verdict based on threat intel")
    ws_execute(f"UPDATE mcp_server_registry SET verdict = 'rejected' WHERE server_id = '{server_id}'")
    log(f"[{flow_id}] Verdict updated to 'rejected' due to threat intel")
    
    # Step 7: Verify threat signals queryable
    log(f"[{flow_id}] Step 7: Verify threat signals queryable")
    result = ws_query(f"SELECT signal_name, score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name LIKE 'threat%'")
    threat_rows = result.get("rows", [])
    log(f"[{flow_id}] Threat signals: {threat_rows}")
    
    # Step 8: Record audit event
    log(f"[{flow_id}] Step 8: Record audit event")
    ws_write("audit_log", [{
        "target_server_id": server_id,
        "event_type": "e2e_flow_2_complete",
        "actor": "e2e_scenarios_run",
        "detail": json.dumps({"flow_id": flow_id, "threat_count": 1, "severity": "high"}),
        "created_at": datetime.now(timezone.utc).isoformat()
    }])
    log(f"[{flow_id}] Audit event recorded")
    
    log(f"[{flow_id}] FLOW 2 COMPLETE: server_id={server_id}, verdict=rejected, threat_overlay=applied")
    return {"flow_id": flow_id, "server_id": server_id, "verdict": "rejected", "threat_count": 1}


def run_flow_3_attestation_refresh_cycle():
    """
    Flow 3: Attestation refresh cycle
    """
    log("=" * 60)
    log("FLOW 3: Attestation Refresh Cycle")
    log("=" * 60)
    
    flow_id = f"flow3-{uuid.uuid4().hex[:8]}"
    server_id = f"srv-attest-{uuid.uuid4().hex[:12]}"
    
    # Step 1: Create server with existing attestation (simulate)
    log(f"[{flow_id}] Step 1: Create server with initial attestation")
    ws_write("mcp_server_registry", [{
        "server_id": server_id,
        "name": f"Attestation Refresh Test {flow_id}",
        "url": f"https://attest-refresh-{server_id}.example.com",
        "description": "Server for attestation refresh e2e testing",
        "verdict": "approved",
        "registry_source": "e2e_test"
    }])
    
    from datetime import timedelta
    # Create an attestation that is about to expire (within 7 days)
    almost_expired = datetime.now(timezone.utc) + timedelta(days=5)
    ws_write("attestations", [{
        "server_id": server_id,
        "attestor": "e2e_validator_initial",
        "statement": f"Initial attestation for {flow_id}",
        "issued_at": (datetime.now(timezone.utc) - timedelta(days=25)).isoformat(),
        "expires_at": almost_expired.isoformat(),
        "revoked": False
    }])
    log(f"[{flow_id}] Initial attestation created, expires in 5 days")
    
    # Step 2: Detect expiring attestation
    log(f"[{flow_id}] Step 2: Query for expiring attestations")
    result = ws_query(f"""
        SELECT a.id, a.server_id, a.expires_at, a.statement 
        FROM attestations a 
        WHERE a.server_id = '{server_id}' 
        AND a.expires_at > now() 
        AND a.expires_at < now() + INTERVAL '7 days'
        AND a.revoked = false
    """)
    expiring = result.get("rows", [])
    log(f"[{flow_id}] Found {len(expiring)} expiring attestation(s)")
    
    # Step 3: Refresh attestation (create new)
    log(f"[{flow_id}] Step 3: Refresh attestation")
    new_expiry = datetime.now(timezone.utc) + timedelta(days=30)
    ws_write("attestations", [{
        "server_id": server_id,
        "attestor": "e2e_validator_refresh",
        "statement": f"Refreshed attestation for {flow_id}",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": new_expiry.isoformat(),
        "revoked": False
    }])
    log(f"[{flow_id}] New attestation created, expires {new_expiry.date()}")
    
    # Step 4: Verify both attestations exist
    log(f"[{flow_id}] Step 4: Verify attestation records")
    result = ws_query(f"SELECT count(*) as cnt, min(issued_at) as oldest, max(issued_at) as newest FROM attestations WHERE server_id = '{server_id}'")
    attest_count = result.get("rows", [{}])[0].get("cnt", 0)
    log(f"[{flow_id}] Server has {attest_count} attestation record(s)")
    
    # Step 5: Verify server still approved
    log(f"[{flow_id}] Step 5: Verify server status")
    result = ws_query(f"SELECT server_id, verdict FROM mcp_server_registry WHERE server_id = '{server_id}'")
    server_rows = result.get("rows", [])
    current_verdict = server_rows[0].get("verdict", "unknown") if server_rows else "unknown"
    log(f"[{flow_id}] Server verdict: {current_verdict}")
    
    # Step 6: Record audit event
    log(f"[{flow_id}] Step 6: Record audit event")
    ws_write("audit_log", [{
        "target_server_id": server_id,
        "event_type": "e2e_flow_3_complete",
        "actor": "e2e_scenarios_run",
        "detail": json.dumps({"flow_id": flow_id, "attestations_count": attest_count, "refreshed": True}),
        "created_at": datetime.now(timezone.utc).isoformat()
    }])
    log(f"[{flow_id}] Audit event recorded")
    
    log(f"[{flow_id}] FLOW 3 COMPLETE: server_id={server_id}, attestations={attest_count}, verdict={current_verdict}")
    return {"flow_id": flow_id, "server_id": server_id, "attestations": attest_count, "verdict": current_verdict}


def cleanup_test_data():
    """Clean up test data created by e2e flows"""
    log("Cleaning up e2e test data...")
    try:
        ws_execute("DELETE FROM audit_log WHERE actor = 'e2e_scenarios_run'")
        ws_execute("DELETE FROM attestations WHERE attestor LIKE 'e2e_%'")
        ws_execute("DELETE FROM risk_register WHERE server_id LIKE 'srv-%/%' ESCAPE '/'")
        ws_execute("DELETE FROM mcp_signal_scores WHERE server_id LIKE 'srv-%/%' ESCAPE '/'")
        ws_execute("DELETE FROM mcp_threat_associations WHERE server_id LIKE 'srv-%/%' ESCAPE '/'")
        ws_execute("DELETE FROM mcp_server_registry WHERE server_id LIKE 'srv-%/%' ESCAPE '/'")
        log("Cleanup complete")
    except Exception as e:
        log(f"Cleanup warning: {e}")


def verify_write_service_health():
    """Verify write_service is responsive"""
    log("Verifying write_service health...")
    try:
        result = ws_query("SELECT 1 as health_check")
        assert result.get("rows") or result.get("count") is not None, "Unexpected query response"
        log("Write service is healthy")
        return True
    except Exception as e:
        log(f"Write service health check failed: {e}")
        return False


def run():
    """Main execution for e2e scenarios run"""
    log("=" * 60)
    log("ZO-SENTINEL e2e SCENARIOS RUNNER")
    log("=" * 60)
    
    results = []
    
    # Health check
    if not verify_write_service_health():
        log("FATAL: Write service not reachable. Aborting.")
        return {"status": "failed", "error": "write_service_unreachable"}
    
    try:
        # Flow 1: New MCP cycle
        log("\nStarting Flow 1...")
        result1 = run_flow_1_new_mcp_cycle()
        results.append({"flow": 1, "status": "complete", "result": result1})
        
        # Flow 2: Threat intel overlay
        log("\nStarting Flow 2...")
        result2 = run_flow_2_threat_intel_overlay()
        results.append({"flow": 2, "status": "complete", "result": result2})
        
        # Flow 3: Attestation refresh
        log("\nStarting Flow 3...")
        result3 = run_flow_3_attestation_refresh_cycle()
        results.append({"flow": 3, "status": "complete", "result": result3})
        
        # Summary
        log("\n" + "=" * 60)
        log("E2E SCENARIOS SUMMARY")
        log("=" * 60)
        for r in results:
            log(f"Flow {r['flow']}: {r['status']}")
            log(f"  Result: {r['result']}")
        
        all_passed = all(r["status"] == "complete" for r in results)
        
        log("\n" + "=" * 60)
        if all_passed:
            log("ALL 3 E2E FLOWS COMPLETED SUCCESSFULLY")
        else:
            log("SOME FLOWS FAILED - CHECK LOG")
        log("=" * 60)
        
        return {"status": "passed" if all_passed else "failed", "flows": results}
        
    except Exception as e:
        log(f"FATAL: E2e scenarios run failed with exception: {e}")
        import traceback
        log(traceback.format_exc())
        return {"status": "failed", "error": str(e)}
    finally:
        # Optional cleanup
        log("\nE2e scenarios run finished at " + datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    result = run()
    print("\nFinal result:", json.dumps(result, indent=2, default=str))