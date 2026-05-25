import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
import os
os.chdir('/home/workspace/zo_sentinel')

import requests
import time
import uuid
from datetime import datetime, timedelta
import json
import hashlib

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
GATEWAY_URL = "http://127.0.0.1:8784"

VERDICTS_BLOCKED = ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "MALICIOUS", "SUSPICIOUS"]
VERDICTS_SAFE = ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "TRUSTED", "VERIFIED", "SAFE", "RECOMMENDED"]

def ws_write(table, rows):
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql):
    payload = {"sql": sql}
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_execute(sql):
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def create_test_server(server_id, name, verdict, trust_score, injection_resilience_score=0.0):
    ws_execute(f"DROP TABLE IF EXISTS mcp_server_registry_test_v2")
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_server_registry_test_v2 (
            server_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            url VARCHAR,
            description VARCHAR,
            trust_score DOUBLE,
            verdict VARCHAR,
            registry_source VARCHAR,
            scan_count INTEGER DEFAULT 0
        )
    """)
    
    ws_write("mcp_server_registry_test_v2", [{
        "server_id": server_id,
        "name": name,
        "url": f"https://test-{server_id}.example.com",
        "description": f"Test server for verdict {verdict}",
        "trust_score": trust_score,
        "verdict": verdict,
        "registry_source": "test",
        "scan_count": 1
    }])
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mcp_signal_scores_test_v2 (
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            evidence VARCHAR,
            scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    ws_write("mcp_signal_scores_test_v2", [{
        "server_id": server_id,
        "signal_name": "injection_resilience",
        "score": injection_resilience_score,
        "evidence": f"Computed at {datetime.now().isoformat()}",
        "scored_at": datetime.now().isoformat()
    }])
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS mesh_events_test_v2 (
            event_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            event_type VARCHAR,
            verdict VARCHAR,
            trust_score DOUBLE,
            injection_resilience_score DOUBLE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def call_gateway_commit(server_id, commit_hash, repository="test/repo", force_commit=False, override_reason=None):
    payload = {
        "server_id": server_id,
        "commit_hash": commit_hash,
        "repository": repository,
        "branch": "main",
        "author": "test@example.com",
        "message": f"Test commit {commit_hash[:8]}",
        "files_changed": ["test.py", "config.json"],
        "force_commit": force_commit,
        "override_reason": override_reason
    }
    
    try:
        resp = requests.post(f"{GATEWAY_URL}/commit", json=payload, timeout=30)
        return {"status_code": resp.status_code, "data": resp.json()}
    except Exception as e:
        return {"status_code": 0, "error": str(e)}

def test_blocked_verdicts():
    results = []
    
    for verdict in ["CAUTION_LIMITED", "HIGH_RISK_ISOLATED"]:
        server_id = f"test-blocked-{verdict.lower().replace('_', '-')}-{uuid.uuid4().hex[:8]}"
        
        trust_score = 0.35 if verdict == "CAUTION_LIMITED" else 0.25
        injection_score = 0.45
        
        create_test_server(
            server_id=server_id,
            name=f"Blocked Test Server {verdict}",
            verdict=verdict,
            trust_score=trust_score,
            injection_resilience_score=injection_score
        )
        
        commit_hash = hashlib.sha256(f"{server_id}-{time.time()}".encode()).hexdigest()[:40]
        result = call_gateway_commit(server_id, commit_hash)
        
        blocked = False
        if result.get("status_code") == 200:
            data = result.get("data", {})
            blocked = data.get("blocked", False)
            status = data.get("status", "unknown")
            injection_included = "injection_resilience_score" in data
        else:
            status = "error"
            blocked = True
        
        passed = blocked
        results.append({
            "verdict": verdict,
            "server_id": server_id,
            "passed": passed,
            "blocked": blocked,
            "status": status,
            "result": result
        })
        
        print(f"[{'PASS' if passed else 'FAIL'}] Verdict {verdict}: blocked={blocked}, status={status}")
    
    return all(r["passed"] for r in results)

def test_allowed_verdicts():
    results = []
    
    for verdict in ["TRUSTED_GENERAL", "TRUSTED_RESEARCH"]:
        server_id = f"test-allowed-{verdict.lower().replace('_', '-')}-{uuid.uuid4().hex[:8]}"
        
        trust_score = 0.85 if verdict == "TRUSTED_GENERAL" else 0.92
        injection_score = 0.88
        
        create_test_server(
            server_id=server_id,
            name=f"Allowed Test Server {verdict}",
            verdict=verdict,
            trust_score=trust_score,
            injection_resilience_score=injection_score
        )
        
        commit_hash = hashlib.sha256(f"{server_id}-{time.time()}".encode()).hexdigest()[:40]
        result = call_gateway_commit(server_id, commit_hash)
        
        allowed = False
        if result.get("status_code") == 200:
            data = result.get("data", {})
            allowed = not data.get("blocked", True)
            status = data.get("status", "unknown")
            injection_included = "injection_resilience_score" in data
        else:
            status = "error"
            allowed = False
        
        passed = allowed
        results.append({
            "verdict": verdict,
            "server_id": server_id,
            "passed": passed,
            "allowed": allowed,
            "status": status,
            "result": result
        })
        
        print(f"[{'PASS' if passed else 'FAIL'}] Verdict {verdict}: allowed={allowed}, status={status}")
    
    return all(r["passed"] for r in results)

def test_injection_resilience_included():
    server_id = f"test-injection-{uuid.uuid4().hex[:8]}"
    injection_score = 0.82
    
    create_test_server(
        server_id=server_id,
        name=f"Injection Test Server",
        verdict="TRUSTED_GENERAL",
        trust_score=0.88,
        injection_resilience_score=injection_score
    )
    
    commit_hash = hashlib.sha256(f"{server_id}-{time.time()}".encode()).hexdigest()[:40]
    result = call_gateway_commit(server_id, commit_hash)
    
    injection_included = False
    if result.get("status_code") == 200:
        data = result.get("data", {})
        injection_included = "injection_resilience_score" in data
        included_score = data.get("injection_resilience_score", 0.0)
        score_match = abs(included_score - injection_score) < 0.01
    else:
        score_match = False
    
    passed = injection_included and score_match
    print(f"[{'PASS' if passed else 'FAIL'}] Injection resilience included: included={injection_included}, score_match={score_match}")
    
    return passed

def test_force_commit_overrides():
    server_id = f"test-force-{uuid.uuid4().hex[:8]}"
    
    create_test_server(
        server_id=server_id,
        name="Force Commit Test Server",
        verdict="CAUTION_LIMITED",
        trust_score=0.35,
        injection_resilience_score=0.72
    )
    
    commit_hash = hashlib.sha256(f"{server_id}-{time.time()}".encode()).hexdigest()[:40]
    
    result_normal = call_gateway_commit(server_id, commit_hash, force_commit=False)
    blocked_normal = result_normal.get("status_code") == 200 and result_normal.get("data", {}).get("blocked", False)
    
    result_force = call_gateway_commit(server_id, commit_hash, force_commit=True, override_reason="Emergency deployment")
    allowed_force = result_force.get("status_code") == 200 and not result_force.get("data", {}).get("blocked", True)
    
    passed = blocked_normal and allowed_force
    print(f"[{'PASS' if passed else 'FAIL'}] Force commit override: normal_blocked={blocked_normal}, force_allowed={allowed_force}")
    
    return passed

def test_mesh_events_forwarded():
    server_id = f"test-mesh-{uuid.uuid4().hex[:8]}"
    injection_score = 0.85
    
    create_test_server(
        server_id=server_id,
        name="Mesh Forward Test Server",
        verdict="TRUSTED_GENERAL",
        trust_score=0.90,
        injection_resilience_score=injection_score
    )
    
    commit_hash = hashlib.sha256(f"{server_id}-{time.time()}".encode()).hexdigest()[:40]
    result = call_gateway_commit(server_id, commit_hash)
    
    event_recorded = False
    if result.get("status_code") == 200:
        ws_query(f"""
            SELECT * FROM mesh_events_test_v2 
            WHERE server_id = '{server_id}' 
            ORDER BY created_at DESC 
            LIMIT 1
        """)
        
        events_result = ws_query(f"SELECT * FROM mesh_events_test_v2 WHERE server_id = '{server_id}'")
        if events_result.get("rows") and len(events_result["rows"]) > 0:
            event = events_result["rows"][0]
            event_recorded = (
                event.get("server_id") == server_id and
                event.get("injection_resilience_score") is not None
            )
    
    passed = event_recorded
    print(f"[{'PASS' if passed else 'FAIL'}] Mesh events forwarded with injection_resilience: recorded={event_recorded}")
    
    return passed

def test_verdict_threshold_enforcement():
    server_id_low = f"test-threshold-low-{uuid.uuid4().hex[:8]}"
    server_id_high = f"test-threshold-high-{uuid.uuid4().hex[:8]}"
    
    create_test_server(
        server_id=server_id_low,
        name="Low Trust Test Server",
        verdict="HIGH_RISK_ISOLATED",
        trust_score=0.20,
        injection_resilience_score=0.30
    )
    
    create_test_server(
        server_id=server_id_high,
        name="High Trust Test Server",
        verdict="TRUSTED_GENERAL",
        trust_score=0.95,
        injection_resilience_score=0.92
    )
    
    commit_low = hashlib.sha256(f"{server_id_low}-{time.time()}".encode()).hexdigest()[:40]
    commit_high = hashlib.sha256(f"{server_id_high}-{time.time()}".encode()).hexdigest()[:40]
    
    result_low = call_gateway_commit(server_id_low, commit_low)
    result_high = call_gateway_commit(server_id_high, commit_high)
    
    blocked_low = result_low.get("status_code") == 200 and result_low.get("data", {}).get("blocked", False)
    allowed_high = result_high.get("status_code") == 200 and not result_high.get("data", {}).get("blocked", True)
    
    passed = blocked_low and allowed_high
    print(f"[{'PASS' if passed else 'FAIL'}] Verdict threshold enforcement: low_blocked={blocked_low}, high_allowed={allowed_high}")
    
    return passed

def cleanup_test_tables():
    ws_execute("DROP TABLE IF EXISTS mcp_server_registry_test_v2")
    ws_execute("DROP TABLE IF EXISTS mcp_signal_scores_test_v2")
    ws_execute("DROP TABLE IF EXISTS mesh_events_test_v2")

def run():
    print("=" * 60)
    print("AIDR Gateway Verdict Test V2 - Integration Wiring")
    print("=" * 60)
    
    results = {}
    
    print("\n[1] Testing BLOCKED verdicts (CAUTION_LIMITED, HIGH_RISK_ISOLATED)...")
    results["blocked_verdicts"] = test_blocked_verdicts()
    
    print("\n[2] Testing ALLOWED verdicts (TRUSTED_GENERAL, TRUSTED_RESEARCH)...")
    results["allowed_verdicts"] = test_allowed_verdicts()
    
    print("\n[3] Testing injection_resilience score inclusion (weight 1.6)...")
    results["injection_included"] = test_injection_resilience_included()
    
    print("\n[4] Testing force_commit override functionality...")
    results["force_override"] = test_force_commit_overrides()
    
    print("\n[5] Testing mesh events forwarding with injection_resilience...")
    results["mesh_forwarded"] = test_mesh_events_forwarded()
    
    print("\n[6] Testing verdict threshold enforcement...")
    results["threshold_enforcement"] = test_verdict_threshold_enforcement()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {test_name}")
    
    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    
    print(f"\nTotal: {passed_count}/{total} tests passed")
    
    cleanup_test_tables()
    
    return passed_count == total

if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)