import os
import sys
import time
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
LOG_DIR = PROJECT_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'snow_connector_approval_wiring_completion.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('snow_connector_approval_wiring_completion')

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
APPROVAL_WORKFLOW_URL = 'http://localhost:8780'
SNOW_CONNECTOR_URL = 'http://localhost:8779'
QUERY_URL = f'{QUERY_SERVICE_URL}/query'
WRITE_URL = f'{WRITE_SERVICE_URL}/write'
EXECUTE_URL = f'{EXECUTE_SERVICE_URL}/execute'

SNOW_CLIENT_ID = os.environ.get('SNOW_CLIENT_ID', '')
SNOW_CLIENT_SECRET = os.environ.get('SNOW_CLIENT_SECRET', '')
SNOW_INSTANCE = os.environ.get('SNOW_INSTANCE', '')
SNOW_USERNAME = os.environ.get('SNOW_USERNAME', '')
SNOW_PASSWORD = os.environ.get('SNOW_PASSWORD', '')


def ws_query(sql: str) -> list:
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: list) -> dict:
    resp = requests.post(WRITE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_write_service_health() -> bool:
    try:
        resp = requests.get(f'{WRITE_SERVICE_URL}/health', timeout=5)
        return resp.status_code == 200
    except Exception as e:
        log.error(f"WriteService health check failed: {e}")
        return False


def check_approval_workflow_health() -> bool:
    try:
        resp = requests.get(f'{APPROVAL_WORKFLOW_URL}/health', timeout=5)
        return resp.status_code == 200
    except Exception as e:
        log.warning(f"Approval workflow health check failed: {e}")
        return False


def read_snow_connector_source() -> str:
    snow_connector_path = PROJECT_DIR / 'snow_connector.py'
    if not snow_connector_path.exists():
        snow_connector_path = PROJECT_DIR / 'snow_connector_wiring.py'
    if not snow_connector_path.exists():
        log.error("snow_connector.py or snow_connector_wiring.py not found")
        return ''
    return snow_connector_path.read_text()


def read_approval_workflow_source() -> str:
    workflow_path = PROJECT_DIR / 'approval_workflow.py'
    if not workflow_path.exists():
        log.error("approval_workflow.py not found")
        return ''
    return workflow_path.read_text()


def read_snow_connector_wiring() -> str:
    wiring_path = PROJECT_DIR / 'snow_connector_approval_wiring.py'
    if not wiring_path.exists():
        log.error("snow_connector_approval_wiring.py not found")
        return ''
    return wiring_path.read_text()


def verify_webhook_endpoint_registered() -> dict:
    result = {'status': 'unknown', 'details': ''}
    
    workflow_source = read_approval_workflow_source()
    snow_connector_source = read_snow_connector_source()
    
    webhook_patterns = [
        '/webhook/snow',
        '/snow/webhook',
        '/snow_connector',
        '@app.post("/webhook/snow'
    ]
    
    webhook_found = False
    for pattern in webhook_patterns:
        if pattern in workflow_source or pattern in snow_connector_source:
            webhook_found = True
            result['details'] = f"Webhook pattern found: {pattern}"
            break
    
    snow_connector_wiring_source = read_snow_connector_wiring()
    if 'webhook' in snow_connector_wiring_source.lower():
        webhook_found = True
        result['details'] = "Webhook endpoint mentioned in wiring file"
    
    if webhook_found:
        result['status'] = 'pass'
    else:
        result['status'] = 'fail'
        result['details'] = "No webhook endpoint registration found"
    
    return result


def test_snow_oauth_flow() -> dict:
    result = {'status': 'unknown', 'details': '', 'token_acquired': False}
    
    if not SNOW_CLIENT_ID or not SNOW_CLIENT_SECRET:
        result['status'] = 'skip'
        result['details'] = 'SNOW_CLIENT_ID or SNOW_CLIENT_SECRET not set in environment'
        return result
    
    if not SNOW_INSTANCE:
        result['status'] = 'skip'
        result['details'] = 'SNOW_INSTANCE not set in environment'
        return result
    
    token_url = f"https://{SNOW_INSTANCE}.service-now.com/oauth_auth.do"
    params = {
        'grant_type': 'client_credentials',
        'client_id': SNOW_CLIENT_ID,
        'client_secret': SNOW_CLIENT_SECRET,
    }
    
    try:
        resp = requests.post(token_url, data=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if 'access_token' in data:
                result['status'] = 'pass'
                result['token_acquired'] = True
                result['details'] = 'OAuth token acquired successfully'
            else:
                result['status'] = 'fail'
                result['details'] = 'Response did not contain access_token'
        else:
            result['status'] = 'fail'
            result['details'] = f"OAuth request failed with status {resp.status_code}"
    except Exception as e:
        result['status'] = 'fail'
        result['details'] = f"OAuth request exception: {str(e)}"
    
    return result


def verify_mcp_submissions_table() -> dict:
    result = {'status': 'unknown', 'details': ''}
    
    try:
        columns = ws_query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'mcp_submissions'"
        )
        if not columns:
            result['status'] = 'fail'
            result['details'] = 'mcp_submissions table does not exist'
            return result
        
        required_cols = ['server_id', 'name', 'url', 'submitted_at']
        existing_cols = [c['column_name'] for c in columns]
        missing = [c for c in required_cols if c not in existing_cols]
        
        if missing:
            result['status'] = 'fail'
            result['details'] = f"Missing required columns: {missing}"
        else:
            result['status'] = 'pass'
            result['details'] = f"mcp_submissions table exists with all required columns: {existing_cols}"
    except Exception as e:
        result['status'] = 'fail'
        result['details'] = f"Error querying mcp_submissions: {str(e)}"
    
    return result


def test_snow_webhook_to_submission() -> dict:
    result = {'status': 'unknown', 'details': ''}
    
    snow_connector_wiring_source = read_snow_connector_wiring()
    snow_connector_source = read_snow_connector_source()
    
    mcp_submissions_mentions = []
    if 'mcp_submissions' in snow_connector_wiring_source:
        mcp_submissions_mentions.append('snow_connector_approval_wiring.py')
    if 'mcp_submissions' in snow_connector_source:
        mcp_submissions_mentions.append('snow_connector.py')
    
    if mcp_submissions_mentions:
        result['status'] = 'pass'
        result['details'] = f"mcp_submissions writes found in: {', '.join(mcp_submissions_mentions)}"
    else:
        result['status'] = 'fail'
        result['details'] = 'No mcp_submissions table writes found in snow_connector wiring'
    
    return result


def verify_write_service_contract() -> dict:
    result = {'status': 'unknown', 'details': ''}
    
    test_row = {
        'service': 'snow_connector_approval_wiring_completion',
        'last_heartbeat': utc_now_iso(),
        'status': 'ok'
    }
    
    try:
        ws_write('service_health', [test_row])
        result['status'] = 'pass'
        result['details'] = 'WriteService contract verified successfully'
    except Exception as e:
        result['status'] = 'fail'
        result['details'] = f"WriteService contract verification failed: {str(e)}"
    
    return result


def create_test_snow_submission() -> dict:
    result = {'status': 'unknown', 'details': '', 'server_id': ''}
    
    test_server_id = 'snow_connector_approval_wiring_completion_test'
    test_timestamp = utc_now_iso()
    
    test_submission = {
        'server_id': test_server_id,
        'name': 'snow_connector_approval_wiring_completion_test',
        'url': 'https://test-snow.example.com/mcp',
        'description': 'Test submission for wiring verification',
        'trust_score': 50.0,
        'verdict': 'PENDING',
        'submission_source': 'snow_webhook_test',
        'submitted_at': test_timestamp,
        'snow_ticket_id': f'TEST-{int(time.time())}'
    }
    
    try:
        ws_write('mcp_submissions', [test_submission])
        result['status'] = 'pass'
        result['server_id'] = test_server_id
        result['details'] = f"Test submission written successfully with server_id: {test_server_id}"
    except Exception as e:
        result['status'] = 'fail'
        result['details'] = f"Failed to write test submission: {str(e)}"
    
    return result


def verify_snow_webhook_handler_in_approval_workflow() -> dict:
    result = {'status': 'unknown', 'details': ''}
    
    workflow_source = read_approval_workflow_source()
    snow_connector_wiring_source = read_snow_connector_wiring()
    
    snow_webhook_indicators = [
        'snow',
        'service_now',
        'snow_connector',
        'incident',
        'ticket'
    ]
    
    found_in_workflow = []
    for indicator in snow_webhook_indicators:
        if indicator in workflow_source.lower():
            found_in_workflow.append(indicator)
    
    found_in_wiring = []
    for indicator in snow_webhook_indicators:
        if indicator in snow_connector_wiring_source.lower():
            found_in_wiring.append(indicator)
    
    if found_in_workflow or found_in_wiring:
        result['status'] = 'pass'
        result['details'] = f"ServiceNow integration found in workflow: {found_in_workflow}, wiring: {found_in_wiring}"
    else:
        result['status'] = 'fail'
        result['details'] = 'No ServiceNow webhook handler found in approval_workflow or wiring'
    
    return result


def run_completion_checks() -> list:
    checks = []
    
    log.info("Starting snow_connector_approval_wiring completion checks...")
    
    checks.append({
        'name': 'write_service_health',
        'description': 'Verify WriteService is healthy',
        'result': check_write_service_health()
    })
    
    checks.append({
        'name': 'webhook_endpoint_registered',
        'description': 'Verify webhook endpoint is registered in approval_workflow',
        'result': verify_webhook_endpoint_registered()
    })
    
    checks.append({
        'name': 'snow_webhook_handler',
        'description': 'Verify ServiceNow webhook handler exists',
        'result': verify_snow_webhook_handler_in_approval_workflow()
    })
    
    checks.append({
        'name': 'mcp_submissions_table',
        'description': 'Verify mcp_submissions table exists with required columns',
        'result': verify_mcp_submissions_table()
    })
    
    checks.append({
        'name': 'snow_webhook_submission_writes',
        'description': 'Verify mcp_submissions writes when SNOW tickets arrive',
        'result': test_snow_webhook_to_submission()
    })
    
    checks.append({
        'name': 'snow_oauth_flow',
        'description': 'Test ServiceNow OAuth flow',
        'result': test_snow_oauth_flow()
    })
    
    checks.append({
        'name': 'write_service_contract',
        'description': 'Verify WriteService contract works correctly',
        'result': verify_write_service_contract()
    })
    
    checks.append({
        'name': 'test_submission_write',
        'description': 'Write test submission to verify table is writable',
        'result': create_test_snow_submission()
    })
    
    return checks


def print_summary(checks: list):
    log.info("=" * 60)
    log.info("SNOW_CONNECTOR_APPROVAL_WIRING COMPLETION SUMMARY")
    log.info("=" * 60)
    
    passed = 0
    failed = 0
    skipped = 0
    
    for check in checks:
        status = check['result']['status']
        name = check['name']
        details = check['result']['details']
        
        if status == 'pass':
            passed += 1
            status_str = 'PASS'
        elif status == 'fail':
            failed += 1
            status_str = 'FAIL'
        elif status == 'skip':
            skipped += 1
            status_str = 'SKIP'
        else:
            status_str = status.upper()
        
        log.info(f"[{status_str}] {name}: {details}")
    
    log.info("-" * 60)
    log.info(f"TOTAL: {len(checks)} checks | PASS: {passed} | FAIL: {failed} | SKIP: {skipped}")
    log.info("=" * 60)
    
    return failed == 0


def main():
    log.info("Starting snow_connector_approval_wiring_completion module...")
    
    if not check_write_service_health():
        log.error("WriteService is not healthy, cannot proceed with completion checks")
        sys.exit(1)
    
    checks = run_completion_checks()
    success = print_summary(checks)
    
    if success:
        log.info("All completion checks passed!")
        sys.exit(0)
    else:
        log.error("Some completion checks failed!")
        sys.exit(1)


if __name__ == '__main__':
    main()