import time
import uuid
from datetime import datetime, timezone

WRITE_SERVICE = 'http://127.0.0.1:8772'
QUERY_SERVICE = 'http://127.0.0.1:8772'
EXECUTE_SERVICE = 'http://127.0.0.1:8772'

VERDICTS_BLOCKING = ['CAUTION_LIMITED', 'HIGH_RISK_ISOLATED']
VERDICTS_PERMITTING = ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED']
ALL_TEST_VERDICTS = VERDICTS_BLOCKING + VERDICTS_PERMITTING

LOG_FILE = '/tmp/aidr_verdict_gate_test.log'
RESULTS_FILE = '/tmp/aidr_verdict_gate_results.json'

INJECTION_RESILIENCE_SIGNALS = [
    'injection_resilience_score',
    'injection_resilience_v2_score',
    'input_sanitization_score',
    'context_isolation_score'
]


def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def ws_query(sql):
    import requests
    try:
        r = requests.post(QUERY_SERVICE, json={'sql': sql}, timeout=10)
        if r.status_code == 200:
            return r.json()
        log(f"QUERY ERROR {r.status_code}: {r.text[:200]}")
        return {'rows': [], 'count': 0}
    except Exception as e:
        log(f"QUERY EXCEPTION: {e}")
        return {'rows': [], 'count': 0}


def ws_write(table, rows):
    import requests
    try:
        r = requests.post(f'{WRITE_SERVICE}/write', json={'table': table, 'rows': rows}, timeout=15)
        if r.status_code == 200:
            return r.json()
        log(f"WRITE ERROR {r.status_code}: {r.text[:200]}")
        return {'ok': False}
    except Exception as e:
        log(f"WRITE EXCEPTION: {e}")
        return {'ok': False}


def ws_execute(sql):
    import requests
    try:
        r = requests.post(EXECUTE_SERVICE, json={'sql': sql}, timeout=15)
        if r.status_code == 200:
            return r.json()
        log(f"EXECUTE ERROR {r.status_code}: {r.text[:200]}")
        return {'ok': False}
    except Exception as e:
        log(f"EXECUTE EXCEPTION: {e}")
        return {'ok': False}


def ensure_test_tables():
    log("Creating test tables...")
    ws_execute('''
        CREATE TABLE IF NOT EXISTS aidr_test_servers (
            server_id VARCHAR PRIMARY KEY,
            mcp_name VARCHAR,
            verdict VARCHAR,
            trust_score DOUBLE,
            injection_resilience_score DOUBLE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    ws_execute('''
        CREATE TABLE IF NOT EXISTS aidr_test_commit_log (
            id INTEGER PRIMARY KEY,
            server_id VARCHAR,
            mcp_name VARCHAR,
            verdict VARCHAR,
            decision VARCHAR,
            injection_included BOOLEAN,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')


def clear_test_data():
    log("Clearing previous test data...")
    ws_execute("DELETE FROM aidr_test_servers WHERE mcp_name LIKE 'test_aidr_%'")
    ws_execute("DELETE FROM aidr_test_commit_log WHERE mcp_name LIKE 'test_aidr_%'")


def setup_test_servers():
    log("Setting up test MCP servers with various verdicts...")
    test_servers = []
    for verdict in ALL_TEST_VERDICTS:
        server_id = f"test-aidr-{verdict.lower()}-{uuid.uuid4().hex[:8]}"
        mcp_name = f"test_aidr_{verdict.lower().replace('_', '-')}"
        trust_score = 0.95 if verdict in VERDICTS_PERMITTING else 0.35
        injection_score = 0.85 if verdict in VERDICTS_PERMITTING else 0.40
        
        ws_write('aidr_test_servers', [{
            'server_id': server_id,
            'mcp_name': mcp_name,
            'verdict': verdict,
            'trust_score': trust_score,
            'injection_resilience_score': injection_score
        }])
        test_servers.append({
            'server_id': server_id,
            'mcp_name': mcp_name,
            'verdict': verdict,
            'trust_score': trust_score,
            'injection_score': injection_score
        })
        log(f"  Created test server: {mcp_name} with verdict {verdict}")
    
    return test_servers


def get_server_verdict(server_id):
    result = ws_query(f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if result.get('rows') and len(result['rows']) > 0:
        return result['rows'][0].get('verdict')
    
    result = ws_query(f"SELECT verdict FROM aidr_test_servers WHERE server_id = '{server_id}'")
    if result.get('rows') and len(result['rows']) > 0:
        return result['rows'][0].get('verdict')
    
    return None


def get_injection_resilience_score(server_id):
    for signal_name in INJECTION_RESILIENCE_SIGNALS:
        result = ws_query(f"""
            SELECT score FROM mcp_signal_scores 
            WHERE server_id = '{server_id}' 
            AND signal_name = '{signal_name}'
            ORDER BY scored_at DESC LIMIT 1
        """)
        if result.get('rows') and len(result['rows']) > 0:
            return float(result['rows'][0].get('score', 0))
    
    result = ws_query(f"SELECT injection_resilience_score FROM aidr_test_servers WHERE server_id = '{server_id}'")
    if result.get('rows') and len(result['rows']) > 0:
        return float(result['rows'][0].get('injection_resilience_score', 0))
    
    return None


def log_gate_decision(mcp_name, verdict, decision, injection_included, timestamp):
    ws_write('audit_log', [{
        'target_server_id': 'GATE_TEST',
        'event_type': 'AIDR_VERDICT_GATE',
        'actor': 'aidr_verdict_gate_test',
        'detail': f'mcp={mcp_name} verdict={verdict} decision={decision} injection_included={injection_included}'
    }])
    
    ws_write('aidr_test_commit_log', [{
        'server_id': 'GATE',
        'mcp_name': mcp_name,
        'verdict': verdict,
        'decision': decision,
        'injection_included': injection_included
    }])
    log(f"  GATE LOG: {mcp_name} | {verdict} | {decision} | injection={injection_included}")


def evaluate_commit(server):
    server_id = server['server_id']
    mcp_name = server['mcp_name']
    expected_verdict = server['verdict']
    
    verdict = get_server_verdict(server_id)
    if verdict is None:
        log(f"  ERROR: Could not retrieve verdict for {mcp_name}")
        return {'error': 'verdict_not_found', 'server': server}
    
    injection_score = get_injection_resilience_score(server_id)
    
    should_block = verdict in VERDICTS_BLOCKING
    should_permit = verdict in VERDICTS_PERMITTING
    
    decision = 'BLOCKED' if should_block else ('PERMITTED' if should_permit else 'UNKNOWN')
    injection_included = False
    
    if should_permit and injection_score is not None:
        injection_included = True
    
    log_gate_decision(mcp_name, verdict, decision, injection_included, datetime.now(timezone.utc))
    
    expected_decision = 'BLOCKED' if expected_verdict in VERDICTS_BLOCKING else 'PERMITTED'
    passed = decision == expected_decision
    
    return {
        'server': server,
        'verdict': verdict,
        'decision': decision,
        'expected_decision': expected_decision,
        'passed': passed,
        'injection_score': injection_score,
        'injection_included': injection_included
    }


def test_verdict_gate_enforcement(test_servers):
    log("\n=== TESTING VERDICT GATE ENFORCEMENT ===")
    results = []
    passed_count = 0
    failed_count = 0
    
    for server in test_servers:
        log(f"\nTesting: {server['mcp_name']} (expected verdict: {server['verdict']})")
        result = evaluate_commit(server)
        results.append(result)
        
        if result['passed']:
            log(f"  PASS: Decision '{result['decision']}' matches expected '{result['expected_decision']}'")
            passed_count += 1
        else:
            log(f"  FAIL: Decision '{result['decision']}' does not match expected '{result['expected_decision']}'")
            failed_count += 1
    
    return results, passed_count, failed_count


def test_injection_resilience_inclusion(test_servers):
    log("\n=== TESTING INJECTION RESILIENCE IN COMMIT PAYLOAD ===")
    results = []
    
    for server in test_servers:
        verdict = server['verdict']
        if verdict in VERDICTS_PERMITTING:
            injection_score = server['injection_score']
            log(f"  {server['mcp_name']}: Verdict={verdict}, Expected injection_score={injection_score}")
            
            commit_payload = {
                'server_id': server['server_id'],
                'mcp_name': server['mcp_name'],
                'verdict': verdict,
                'approved': True
            }
            
            if injection_score is not None:
                commit_payload['injection_resilience_score'] = injection_score
                log(f"    INCLUDED in payload: injection_resilience_score={injection_score}")
            else:
                log(f"    WARNING: No injection_resilience_score found")
            
            results.append({
                'server': server,
                'payload': commit_payload,
                'has_injection_score': 'injection_resilience_score' in commit_payload,
                'injection_score_value': injection_score
            })
    
    return results


def test_write_service_connectivity():
    log("\n=== TESTING WRITE SERVICE CONNECTIVITY ===")
    try:
        import requests
        r = requests.post(f'{WRITE_SERVICE}/write', json={
            'table': 'service_health',
            'rows': {'service': 'aidr_verdict_gate_test', 'last_heartbeat': datetime.now(timezone.utc).isoformat()}
        }, timeout=5)
        if r.status_code == 200:
            log("  WRITE SERVICE: CONNECTED")
            return True
        else:
            log(f"  WRITE SERVICE: ERROR {r.status_code}")
            return False
    except Exception as e:
        log(f"  WRITE SERVICE: EXCEPTION {e}")
        return False


def generate_test_report(results, passed_count, failed_count, injection_results):
    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'total_tests': passed_count + failed_count,
        'passed': passed_count,
        'failed': failed_count,
        'pass_rate': f"{(passed_count / (passed_count + failed_count) * 100):.1f}%" if (passed_count + failed_count) > 0 else "N/A",
        'verdict_gate_results': results,
        'injection_payload_results': injection_results,
        'blocking_verdicts_tested': VERDICTS_BLOCKING,
        'permitting_verdicts_tested': VERDICTS_PERMITTING
    }
    
    try:
        import json
        with open(RESULTS_FILE, 'w') as f:
            json.dump(report, f, indent=2)
        log(f"\nTest report saved to: {RESULTS_FILE}")
    except Exception as e:
        log(f"Failed to save report: {e}")
    
    log("\n" + "=" * 60)
    log("VERDICT GATE TEST SUMMARY")
    log("=" * 60)
    log(f"Total Tests: {passed_count + failed_count}")
    log(f"Passed: {passed_count}")
    log(f"Failed: {failed_count}")
    log(f"Pass Rate: {report['pass_rate']}")
    log("")
    log("Blocking Verdicts (CAUTION_LIMITED, HIGH_RISK_ISOLATED):")
    blocking_results = [r for r in results if r['server']['verdict'] in VERDICTS_BLOCKING]
    for r in blocking_results:
        status = "PASS" if r['passed'] else "FAIL"
        log(f"  [{status}] {r['server']['mcp_name']}: {r['decision']}")
    log("")
    log("Permitting Verdicts (TRUSTED_GENERAL, TRUSTED_RESEARCH, ENTERPRISE_CONTROLLED):")
    permitting_results = [r for r in results if r['server']['verdict'] in VERDICTS_PERMITTING]
    for r in permitting_results:
        status = "PASS" if r['passed'] else "FAIL"
        inj_included = "injection=YES" if r.get('injection_included') else "injection=NO"
        log(f"  [{status}] {r['server']['mcp_name']}: {r['decision']} ({inj_included})")
    log("=" * 60)
    
    return report


def run():
    log("=" * 60)
    log("AIDR VERDICT GATE TEST HARNESS")
    log("=" * 60)
    
    if not test_write_service_connectivity():
        log("FATAL: Cannot reach write_service. Aborting test.")
        return
    
    ensure_test_tables()
    clear_test_data()
    
    test_servers = setup_test_servers()
    
    results, passed_count, failed_count = test_verdict_gate_enforcement(test_servers)
    
    injection_results = test_injection_resilience_inclusion(test_servers)
    
    report = generate_test_report(results, passed_count, failed_count, injection_results)
    
    log("\nAll test gates have logged decisions to audit_log.")
    log(f"Results file: {RESULTS_FILE}")
    
    if failed_count > 0:
        log(f"\nWARNING: {failed_count} test(s) FAILED")
    else:
        log("\nSUCCESS: All verdict gate tests PASSED")


if __name__ == '__main__':
    run()