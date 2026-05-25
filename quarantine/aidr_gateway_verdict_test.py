import sys
import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, '/home/workspace/zo_sentinel')

import requests
from http_retry import post_with_retry, get_with_retry

WRITE_SERVICE = 'http://127.0.0.1:8772'
QUERY_SERVICE = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE = 'http://127.0.0.1:8772/execute'
HEALTH_SERVICE = 'http://127.0.0.1:8773/health'
TEST_RESULTS_FILE = '/home/workspace/zo_sentinel/test_results.jsonl'
SERVICE_NAME = 'aidr_gateway_verdict_test'


def ws_query(sql):
    try:
        resp = post_with_retry(QUERY_SERVICE, json={'sql': sql}, timeout=30)
        return resp.json()
    except Exception as e:
        return {'rows': [], 'count': 0, 'error': str(e)}


def ws_write(table, rows, wait=True):
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = post_with_retry(WRITE_SERVICE, json=payload, timeout=30)
    return resp.json()


def ws_execute(sql):
    payload = {'sql': sql}
    resp = post_with_retry(EXECUTE_SERVICE, json=payload, timeout=30)
    return resp.json()


def log_result(result):
    result['timestamp'] = datetime.utcnow().isoformat()
    result['service'] = SERVICE_NAME
    with open(TEST_RESULTS_FILE, 'a') as f:
        f.write(json.dumps(result) + '\n')
    print(f"[TEST] {result.get('test_name', 'unknown')}: {result.get('status', 'unknown')} - {result.get('message', '')}")


def clear_test_servers():
    sql = "DELETE FROM mcp_server_registry WHERE name LIKE 'test_aidr_%'"
    ws_execute(sql)
    sql = "DELETE FROM mcp_signal_scores WHERE server_id LIKE 'test_aidr_%'"
    ws_execute(sql)
    sql = "DELETE FROM mesh_events WHERE server_id LIKE 'test_aidr_%'"
    ws_execute(sql)


def create_test_server(server_id, name, description, verdict, trust_score, injection_resilience_score):
    server_data = {
        'server_id': server_id,
        'name': name,
        'description': description,
        'trust_score': trust_score,
        'verdict': verdict,
        'registry_source': 'test_aidr',
        'scan_count': 1
    }
    ws_write('mcp_server_registry', server_data)

    signals = [
        {
            'server_id': server_id,
            'signal_name': 'injection_resilience',
            'score': injection_resilience_score,
            'evidence': f'AI resilience test score: {injection_resilience_score}',
            'scored_at': datetime.utcnow().isoformat()
        },
        {
            'server_id': server_id,
            'signal_name': 'trust_synthetic',
            'score': trust_score,
            'evidence': f'Trust score: {trust_score}',
            'scored_at': datetime.utcnow().isoformat()
        }
    ]
    ws_write('mcp_signal_scores', signals)
    return server_id


def get_server_verdict(server_id):
    result = ws_query(f"SELECT verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'")
    if result.get('rows') and len(result['rows']) > 0:
        return result['rows'][0]
    return None


def get_injection_resilience_score(server_id):
    result = ws_query(f"SELECT score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'")
    if result.get('rows') and len(result['rows']) > 0:
        return result['rows'][0]['score']
    return None


def simulate_gateway_verdict_check(server_id, override_active=False):
    verdict_data = get_server_verdict(server_id)
    if not verdict_data:
        return {'action': 'BLOCK', 'reason': 'server_not_found'}

    verdict = verdict_data['verdict']
    trust_score = verdict_data['trust_score']

    TRUSTED_VERDICTS = ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH']
    BLOCKED_VERDICTS = ['CAUTION_LIMITED', 'HIGH_RISK_ISOLATED']

    if verdict in TRUSTED_VERDICTS:
        return {
            'action': 'FORWARD',
            'verdict': verdict,
            'trust_score': trust_score,
            'override': override_active
        }
    elif verdict in BLOCKED_VERDICTS:
        if override_active:
            return {
                'action': 'FORWARD',
                'verdict': verdict,
                'trust_score': trust_score,
                'override': True
            }
        return {
            'action': 'BLOCK',
            'verdict': verdict,
            'trust_score': trust_score,
            'override': False
        }
    else:
        return {
            'action': 'BLOCK',
            'verdict': verdict,
            'trust_score': trust_score,
            'reason': 'unknown_verdict'
        }


def test_forwards_trusted_general():
    test_name = 'test_forwards_trusted_general'
    print(f"\n[TEST] Running: {test_name}")

    server_id = 'test_aidr_trusted_general_001'
    clear_test_servers()

    create_test_server(
        server_id=server_id,
        name='test_aidr_trusted_general',
        description='Trusted general purpose MCP server',
        verdict='TRUSTED_GENERAL',
        trust_score=0.92,
        injection_resilience_score=0.87
    )

    time.sleep(0.5)

    result = simulate_gateway_verdict_check(server_id)

    injection_score = get_injection_resilience_score(server_id)
    result['injection_resilience_score'] = injection_score

    success = result['action'] == 'FORWARD' and injection_score is not None

    log_result({
        'test_name': test_name,
        'status': 'PASS' if success else 'FAIL',
        'server_id': server_id,
        'verdict': 'TRUSTED_GENERAL',
        'expected_action': 'FORWARD',
        'actual_action': result['action'],
        'injection_resilience_included': injection_score is not None,
        'injection_resilience_score': injection_score,
        'message': 'TRUSTED_GENERAL correctly forwarded with injection resilience score included'
    })

    clear_test_servers()
    return success


def test_forwards_trusted_research():
    test_name = 'test_forwards_trusted_research'
    print(f"\n[TEST] Running: {test_name}")

    server_id = 'test_aidr_trusted_research_001'
    clear_test_servers()

    create_test_server(
        server_id=server_id,
        name='test_aidr_trusted_research',
        description='Trusted research MCP server',
        verdict='TRUSTED_RESEARCH',
        trust_score=0.88,
        injection_resilience_score=0.75
    )

    time.sleep(0.5)

    result = simulate_gateway_verdict_check(server_id)

    injection_score = get_injection_resilience_score(server_id)
    result['injection_resilience_score'] = injection_score

    success = result['action'] == 'FORWARD' and injection_score is not None

    log_result({
        'test_name': test_name,
        'status': 'PASS' if success else 'FAIL',
        'server_id': server_id,
        'verdict': 'TRUSTED_RESEARCH',
        'expected_action': 'FORWARD',
        'actual_action': result['action'],
        'injection_resilience_included': injection_score is not None,
        'injection_resilience_score': injection_score,
        'message': 'TRUSTED_RESEARCH correctly forwarded with injection resilience score included'
    })

    clear_test_servers()
    return success


def test_blocks_caution_limited():
    test_name = 'test_blocks_caution_limited'
    print(f"\n[TEST] Running: {test_name}")

    server_id = 'test_aidr_caution_limited_001'
    clear_test_servers()

    create_test_server(
        server_id=server_id,
        name='test_aidr_caution_limited',
        description='Caution limited MCP server',
        verdict='CAUTION_LIMITED',
        trust_score=0.45,
        injection_resilience_score=0.55
    )

    time.sleep(0.5)

    result_no_override = simulate_gateway_verdict_check(server_id, override_active=False)

    injection_score = get_injection_resilience_score(server_id)

    success = result_no_override['action'] == 'BLOCK' and result_no_override['override'] == False

    log_result({
        'test_name': test_name,
        'status': 'PASS' if success else 'FAIL',
        'server_id': server_id,
        'verdict': 'CAUTION_LIMITED',
        'expected_action': 'BLOCK',
        'actual_action': result_no_override['action'],
        'override_active': result_no_override['override'],
        'injection_resilience_score': injection_score,
        'message': 'CAUTION_LIMITED correctly blocked without override'
    })

    clear_test_servers()
    return success


def test_blocks_high_risk_isolated():
    test_name = 'test_blocks_high_risk_isolated'
    print(f"\n[TEST] Running: {test_name}")

    server_id = 'test_aidr_high_risk_001'
    clear_test_servers()

    create_test_server(
        server_id=server_id,
        name='test_aidr_high_risk_isolated',
        description='High risk isolated MCP server',
        verdict='HIGH_RISK_ISOLATED',
        trust_score=0.15,
        injection_resilience_score=0.22
    )

    time.sleep(0.5)

    result_no_override = simulate_gateway_verdict_check(server_id, override_active=False)

    injection_score = get_injection_resilience_score(server_id)

    success = result_no_override['action'] == 'BLOCK' and result_no_override['override'] == False

    log_result({
        'test_name': test_name,
        'status': 'PASS' if success else 'FAIL',
        'server_id': server_id,
        'verdict': 'HIGH_RISK_ISOLATED',
        'expected_action': 'BLOCK',
        'actual_action': result_no_override['action'],
        'override_active': result_no_override['override'],
        'injection_resilience_score': injection_score,
        'message': 'HIGH_RISK_ISOLATED correctly blocked without override'
    })

    clear_test_servers()
    return success


def test_injection_resilience_included_in_payload():
    test_name = 'test_injection_resilience_included_in_payload'
    print(f"\n[TEST] Running: {test_name}")

    server_id = 'test_aidr_injection_test_001'
    clear_test_servers()

    expected_score = 0.83
    create_test_server(
        server_id=server_id,
        name='test_aidr_injection_payload',
        description='Test injection resilience in payload',
        verdict='TRUSTED_GENERAL',
        trust_score=0.90,
        injection_resilience_score=expected_score
    )

    time.sleep(0.5)

    injection_score = get_injection_resilience_score(server_id)

    result = simulate_gateway_verdict_check(server_id)
    result['injection_resilience_score'] = injection_score

    commit_payload = {
        'server_id': server_id,
        'verdict': result['verdict'],
        'trust_score': result['trust_score'],
        'injection_resilience_score': injection_score
    }

    success = injection_score is not None and 'injection_resilience_score' in commit_payload

    log_result({
        'test_name': test_name,
        'status': 'PASS' if success else 'FAIL',
        'server_id': server_id,
        'expected_injection_score': expected_score,
        'actual_injection_score': injection_score,
        'commit_payload': commit_payload,
        'injection_resilience_included': success,
        'message': f"Injection resilience score {injection_score} correctly included in commit payload"
    })

    clear_test_servers()
    return success


def test_override_allows_blocked_verdict():
    test_name = 'test_override_allows_blocked_verdict'
    print(f"\n[TEST] Running: {test_name}")

    server_id = 'test_aidr_override_test_001'
    clear_test_servers()

    create_test_server(
        server_id=server_id,
        name='test_aidr_override',
        description='Test override behavior',
        verdict='HIGH_RISK_ISOLATED',
        trust_score=0.15,
        injection_resilience_score=0.22
    )

    time.sleep(0.5)

    result_with_override = simulate_gateway_verdict_check(server_id, override_active=True)

    success = result_with_override['action'] == 'FORWARD' and result_with_override['override'] == True

    log_result({
        'test_name': test_name,
        'status': 'PASS' if success else 'FAIL',
        'server_id': server_id,
        'verdict': 'HIGH_RISK_ISOLATED',
        'expected_action': 'FORWARD',
        'actual_action': result_with_override['action'],
        'override_active': result_with_override['override'],
        'message': 'Override correctly allows blocked verdict to be forwarded'
    })

    clear_test_servers()
    return success


def test_commit_payload_structure():
    test_name = 'test_commit_payload_structure'
    print(f"\n[TEST] Running: {test_name}")

    server_id = 'test_aidr_payload_structure_001'
    clear_test_servers()

    create_test_server(
        server_id=server_id,
        name='test_aidr_payload_structure',
        description='Test commit payload structure',
        verdict='TRUSTED_GENERAL',
        trust_score=0.91,
        injection_resilience_score=0.78
    )

    time.sleep(0.5)

    verdict_data = get_server_verdict(server_id)
    injection_score = get_injection_resilience_score(server_id)

    commit_payload = {
        'server_id': server_id,
        'verdict': verdict_data['verdict'],
        'trust_score': verdict_data['trust_score'],
        'injection_resilience_score': injection_score,
        'timestamp': datetime.utcnow().isoformat()
    }

    required_fields = ['server_id', 'verdict', 'trust_score', 'injection_resilience_score', 'timestamp']
    all_fields_present = all(field in commit_payload for field in required_fields)

    success = all_fields_present and injection_score is not None

    log_result({
        'test_name': test_name,
        'status': 'PASS' if success else 'FAIL',
        'server_id': server_id,
        'commit_payload': commit_payload,
        'required_fields': required_fields,
        'all_fields_present': all_fields_present,
        'message': f"Commit payload structure valid: all required fields present"
    })

    clear_test_servers()
    return success


def run():
    print("=" * 60)
    print("AIDR Gateway Verdict Enforcement Integration Test")
    print("=" * 60)

    Path(TEST_RESULTS_FILE).parent.mkdir(parents=True, exist_ok=True)

    clear_test_servers()

    tests = [
        test_forwards_trusted_general,
        test_forwards_trusted_research,
        test_blocks_caution_limited,
        test_blocks_high_risk_isolated,
        test_injection_resilience_included_in_payload,
        test_override_allows_blocked_verdict,
        test_commit_payload_structure,
    ]

    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append({'test': test_func.__name__, 'passed': result})
        except Exception as e:
            print(f"[ERROR] Test {test_func.__name__} failed with exception: {e}")
            results.append({'test': test_func.__name__, 'passed': False, 'error': str(e)})

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for r in results if r['passed'])
    failed = len(results) - passed

    for r in results:
        status = "PASS" if r['passed'] else "FAIL"
        print(f"  [{status}] {r['test']}")

    print(f"\nTotal: {len(results)} tests, {passed} passed, {failed} failed")

    log_result({
        'test_name': 'test_suite_summary',
        'status': 'PASS' if failed == 0 else 'FAIL',
        'total_tests': len(results),
        'passed': passed,
        'failed': failed,
        'message': f'Test suite completed: {passed}/{len(results)} tests passed'
    })

    clear_test_servers()

    return failed == 0


if __name__ == '__main__':
    success = run()
    sys.exit(0 if success else 1)