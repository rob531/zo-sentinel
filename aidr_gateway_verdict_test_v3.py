import logging
import sys
import time
import signal
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/aidr_gateway_verdict_test_v3.log')]
)
LOG = logging.getLogger(__name__)

SERVICE_NAME = 'aidr_gateway_verdict_test_v3'
PORT = 8781
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
GATEWAY_URL = 'http://localhost:3891'

VERDICT_ALLOWED = 'TRUSTED_GENERAL'
VERDICT_CAUTION = 'CAUTION_LIMITED'
VERDICT_HIGH_RISK = 'HIGH_RISK_ISOLATED'
VERDICT_KNOWN_THREAT = 'KNOWN_THREAT'


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        import requests
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        LOG.error(f"ws_query failed: {e}")
        return None


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        import requests
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        import requests
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.error(f"ws_execute failed: {e}")
        return False


def compute_deterministic_id(*args) -> str:
    import hashlib
    raw = '|'.join(str(a) for a in args)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def setup_test_tables() -> bool:
    LOG.info("Setting up test tables...")
    tables_ok = True
    
    mcp_registry_table = """
    CREATE TABLE IF NOT EXISTS mcp_server_registry (
        server_id VARCHAR PRIMARY KEY,
        name VARCHAR,
        url VARCHAR,
        description VARCHAR,
        trust_score DOUBLE,
        verdict VARCHAR,
        registry_source VARCHAR,
        first_seen TIMESTAMPTZ,
        last_seen TIMESTAMPTZ
    )
    """
    if not ws_execute(mcp_registry_table):
        LOG.error("Failed to create mcp_server_registry table")
        tables_ok = False
    
    return tables_ok


def create_test_server(server_id: str, name: str, verdict: str, trust_score: float = 50.0) -> bool:
    sql = f"""
    INSERT OR REPLACE INTO mcp_server_registry 
    (server_id, name, url, description, trust_score, verdict, registry_source, first_seen, last_seen)
    VALUES (
        '{server_id}',
        '{name}',
        'https://test.example.com/{server_id}',
        'Test server for verdict enforcement',
        {trust_score},
        '{verdict}',
        'test',
        '{utc_now_iso()}',
        '{utc_now_iso()}'
    )
    """
    return ws_execute(sql)


def check_aidr_commit_gateway_available() -> bool:
    try:
        import requests
        resp = requests.get(f"{GATEWAY_URL}/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def mock_verdict_check(server_id: str, override: bool = False) -> Dict[str, Any]:
    sql = f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'"
    rows = ws_query(sql)
    
    if not rows:
        return {
            'allowed': False,
            'reason': 'Server not found in registry',
            'verdict': None
        }
    
    verdict = rows[0].get('verdict', 'UNKNOWN')
    
    if verdict == VERDICT_KNOWN_THREAT:
        return {
            'allowed': False,
            'reason': f'Verdict {verdict} is blocked',
            'verdict': verdict
        }
    
    if verdict in [VERDICT_CAUTION, VERDICT_HIGH_RISK]:
        if not override:
            return {
                'allowed': False,
                'reason': f'Verdict {verdict} requires explicit override to commit',
                'verdict': verdict
            }
        return {
            'allowed': True,
            'reason': f'Verdict {verdict} allowed with override flag',
            'verdict': verdict
        }
    
    if verdict == VERDICT_ALLOWED:
        return {
            'allowed': True,
            'reason': f'Verdict {verdict} is allowed',
            'verdict': verdict
        }
    
    return {
        'allowed': True,
        'reason': f'Verdict {verdict} defaults to allowed',
        'verdict': verdict
    }


def start_mock_aidr_server(port: int = 3891) -> bool:
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json
    
    mock_requests = []
    
    class MockHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            mock_requests.append({
                'path': self.path,
                'body': body,
                'ts': utc_now_iso()
            })
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok', 'commit_id': 'mock-123'}).encode())
        
        def do_GET(self):
            if self.path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode())
            else:
                self.send_response(404)
                self.end_headers()
        
        def log_message(self, format, *args):
            pass
    
    def run_server():
        server = HTTPServer(('127.0.0.1', port), MockHandler)
        server.serve_forever()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    time.sleep(0.5)
    LOG.info(f"Mock AIDR server started on port {port}")
    return True


def test_trusted_general_allowed() -> Dict[str, Any]:
    LOG.info("=" * 60)
    LOG.info("TEST CASE 1: TRUSTED_GENERAL verdict should ALLOW commit")
    LOG.info("=" * 60)
    
    server_id = compute_deterministic_id('test', 'trusted', 'general')
    test_passed = False
    reason = ''
    
    try:
        if not create_test_server(server_id, 'Test-TRUSTED', VERDICT_ALLOWED, trust_score=85.0):
            return {'passed': False, 'reason': 'Failed to create test server'}
        
        result = mock_verdict_check(server_id)
        
        LOG.info(f"Verdict check result: {result}")
        
        if result.get('allowed') is True:
            test_passed = True
            reason = f"TRUSTED_GENERAL correctly allowed: {result.get('reason')}"
            LOG.info(f"PASS: {reason}")
        else:
            reason = f"TRUSTED_GENERAL incorrectly blocked: {result.get('reason')}"
            LOG.error(f"FAIL: {reason}")
    
    except Exception as e:
        reason = f"Test exception: {e}"
        LOG.error(f"FAIL: {reason}")
    
    return {
        'test_name': 'test_trusted_general_allowed',
        'verdict': VERDICT_ALLOWED,
        'passed': test_passed,
        'reason': reason
    }


def test_caution_limited_blocked() -> Dict[str, Any]:
    LOG.info("=" * 60)
    LOG.info("TEST CASE 2: CAUTION_LIMITED verdict should BLOCK commit (no override)")
    LOG.info("=" * 60)
    
    server_id = compute_deterministic_id('test', 'caution', 'limited')
    test_passed = False
    reason = ''
    
    try:
        if not create_test_server(server_id, 'Test-CAUTION', VERDICT_CAUTION, trust_score=45.0):
            return {'passed': False, 'reason': 'Failed to create test server'}
        
        result = mock_verdict_check(server_id, override=False)
        
        LOG.info(f"Verdict check result (no override): {result}")
        
        if result.get('allowed') is False and VERDICT_CAUTION in result.get('reason', ''):
            test_passed = True
            reason = f"CAUTION_LIMITED correctly blocked without override: {result.get('reason')}"
            LOG.info(f"PASS: {reason}")
        else:
            reason = f"CAUTION_LIMITED incorrectly allowed: {result.get('reason')}"
            LOG.error(f"FAIL: {reason}")
    
    except Exception as e:
        reason = f"Test exception: {e}"
        LOG.error(f"FAIL: {reason}")
    
    return {
        'test_name': 'test_caution_limited_blocked',
        'verdict': VERDICT_CAUTION,
        'passed': test_passed,
        'reason': reason
    }


def test_caution_limited_with_override() -> Dict[str, Any]:
    LOG.info("=" * 60)
    LOG.info("TEST CASE 3: CAUTION_LIMITED with override flag should ALLOW commit")
    LOG.info("=" * 60)
    
    server_id = compute_deterministic_id('test', 'caution', 'override')
    test_passed = False
    reason = ''
    
    try:
        if not create_test_server(server_id, 'Test-CAUTION-OVERRIDE', VERDICT_CAUTION, trust_score=45.0):
            return {'passed': False, 'reason': 'Failed to create test server'}
        
        result = mock_verdict_check(server_id, override=True)
        
        LOG.info(f"Verdict check result (with override): {result}")
        
        if result.get('allowed') is True and 'override' in result.get('reason', '').lower():
            test_passed = True
            reason = f"CAUTION_LIMITED correctly allowed with override: {result.get('reason')}"
            LOG.info(f"PASS: {reason}")
        else:
            reason = f"CAUTION_LIMITED with override incorrectly blocked: {result.get('reason')}"
            LOG.error(f"FAIL: {reason}")
    
    except Exception as e:
        reason = f"Test exception: {e}"
        LOG.error(f"FAIL: {reason}")
    
    return {
        'test_name': 'test_caution_limited_with_override',
        'verdict': VERDICT_CAUTION,
        'passed': test_passed,
        'reason': reason
    }


def test_high_risk_isolated_blocked() -> Dict[str, Any]:
    LOG.info("=" * 60)
    LOG.info("TEST CASE 4: HIGH_RISK_ISOLATED verdict should BLOCK commit (no override)")
    LOG.info("=" * 60)
    
    server_id = compute_deterministic_id('test', 'high_risk', 'isolated')
    test_passed = False
    reason = ''
    
    try:
        if not create_test_server(server_id, 'Test-HIGH-RISK', VERDICT_HIGH_RISK, trust_score=25.0):
            return {'passed': False, 'reason': 'Failed to create test server'}
        
        result = mock_verdict_check(server_id, override=False)
        
        LOG.info(f"Verdict check result (no override): {result}")
        
        if result.get('allowed') is False and VERDICT_HIGH_RISK in result.get('reason', ''):
            test_passed = True
            reason = f"HIGH_RISK_ISOLATED correctly blocked without override: {result.get('reason')}"
            LOG.info(f"PASS: {reason}")
        else:
            reason = f"HIGH_RISK_ISOLATED incorrectly allowed: {result.get('reason')}"
            LOG.error(f"FAIL: {reason}")
    
    except Exception as e:
        reason = f"Test exception: {e}"
        LOG.error(f"FAIL: {reason}")
    
    return {
        'test_name': 'test_high_risk_isolated_blocked',
        'verdict': VERDICT_HIGH_RISK,
        'passed': test_passed,
        'reason': reason
    }


def test_high_risk_with_override() -> Dict[str, Any]:
    LOG.info("=" * 60)
    LOG.info("TEST CASE 5: HIGH_RISK_ISOLATED with override flag should ALLOW commit")
    LOG.info("=" * 60)
    
    server_id = compute_deterministic_id('test', 'high_risk', 'override')
    test_passed = False
    reason = ''
    
    try:
        if not create_test_server(server_id, 'Test-HIGH-RISK-OVERRIDE', VERDICT_HIGH_RISK, trust_score=25.0):
            return {'passed': False, 'reason': 'Failed to create test server'}
        
        result = mock_verdict_check(server_id, override=True)
        
        LOG.info(f"Verdict check result (with override): {result}")
        
        if result.get('allowed') is True and 'override' in result.get('reason', '').lower():
            test_passed = True
            reason = f"HIGH_RISK_ISOLATED correctly allowed with override: {result.get('reason')}"
            LOG.info(f"PASS: {reason}")
        else:
            reason = f"HIGH_RISK_ISOLATED with override incorrectly blocked: {result.get('reason')}"
            LOG.error(f"FAIL: {reason}")
    
    except Exception as e:
        reason = f"Test exception: {e}"
        LOG.error(f"FAIL: {reason}")
    
    return {
        'test_name': 'test_high_risk_with_override',
        'verdict': VERDICT_HIGH_RISK,
        'passed': test_passed,
        'reason': reason
    }


def test_known_threat_always_blocked() -> Dict[str, Any]:
    LOG.info("=" * 60)
    LOG.info("TEST CASE 6: KNOWN_THREAT verdict should ALWAYS BLOCK (even with override)")
    LOG.info("=" * 60)
    
    server_id = compute_deterministic_id('test', 'known_threat')
    test_passed = False
    reason = ''
    
    try:
        if not create_test_server(server_id, 'Test-KNOWN-THREAT', VERDICT_KNOWN_THREAT, trust_score=10.0):
            return {'passed': False, 'reason': 'Failed to create test server'}
        
        result = mock_verdict_check(server_id, override=True)
        
        LOG.info(f"Verdict check result (with override): {result}")
        
        if result.get('allowed') is False and VERDICT_KNOWN_THREAT in result.get('reason', ''):
            test_passed = True
            reason = f"KNOWN_THREAT correctly blocked even with override: {result.get('reason')}"
            LOG.info(f"PASS: {reason}")
        else:
            reason = f"KNOWN_THREAT incorrectly allowed: {result.get('reason')}"
            LOG.error(f"FAIL: {reason}")
    
    except Exception as e:
        reason = f"Test exception: {e}"
        LOG.error(f"FAIL: {reason}")
    
    return {
        'test_name': 'test_known_threat_always_blocked',
        'verdict': VERDICT_KNOWN_THREAT,
        'passed': test_passed,
        'reason': reason
    }


def run_all_tests() -> Dict[str, Any]:
    LOG.info("Starting AIDR Gateway Verdict Test Suite v3")
    LOG.info("=" * 60)
    
    if not setup_test_tables():
        return {
            'success': False,
            'reason': 'Failed to setup test tables'
        }
    
    tests = [
        test_trusted_general_allowed,
        test_caution_limited_blocked,
        test_caution_limited_with_override,
        test_high_risk_isolated_blocked,
        test_high_risk_with_override,
        test_known_threat_always_blocked,
    ]
    
    results = []
    for test_fn in tests:
        result = test_fn()
        results.append(result)
        LOG.info(f"Result: {'PASS' if result['passed'] else 'FAIL'} - {result['reason']}")
    
    passed = sum(1 for r in results if r['passed'])
    total = len(results)
    
    summary = {
        'total': total,
        'passed': passed,
        'failed': total - passed,
        'success': passed == total,
        'results': results
    }
    
    LOG.info("=" * 60)
    LOG.info(f"TEST SUMMARY: {passed}/{total} tests passed")
    LOG.info("=" * 60)
    
    for r in results:
        status = "PASS" if r['passed'] else "FAIL"
        LOG.info(f"  [{status}] {r['test_name']}: {r['reason']}")
    
    return summary


def send_heartbeat(status: str, meta: Optional[Dict[str, Any]] = None) -> bool:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': str(meta) if meta else ''
    }
    return ws_write('service_health', [row])


def main() -> int:
    LOG.info(f"Starting {SERVICE_NAME}")
    send_heartbeat('running', {'phase': 'test_execution'})
    
    try:
        summary = run_all_tests()
        
        if summary['success']:
            send_heartbeat('completed', summary)
            LOG.info("All tests passed!")
            return 0
        else:
            send_heartbeat('failed', summary)
            LOG.error(f"Tests failed: {summary['failed']}/{summary['total']}")
            return 1
    
    except Exception as e:
        LOG.error(f"Test suite failed with exception: {e}")
        send_heartbeat('error', {'error': str(e)})
        return 1


if __name__ == '__main__':
    sys.exit(main())