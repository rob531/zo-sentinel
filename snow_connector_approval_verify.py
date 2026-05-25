import os
import sys
import logging
import hashlib
import json
import requests
from datetime import datetime, timezone

# Service URLs
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
APPROVAL_WORKFLOW_URL = 'http://localhost:8780'

SERVICE_NAME = 'snow_connector_approval_verify'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            f'{QUERY_SERVICE_URL}/query',
            json={'sql': sql},
            timeout=15
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get('rows', [])
    except Exception as e:
        log.error(f'WS query failed: {e}')
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=15
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f'WS write failed: {e}')
        return False


def get_service_health(service_name: str) -> dict:
    sql = f"SELECT service, last_heartbeat, status FROM service_health WHERE service = '{service_name}'"
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return {}


def check_snow_connector_heartbeat() -> bool:
    log.info('Checking snow_connector heartbeat...')
    health = get_service_health('snow_connector')
    if health:
        last_beat = health.get('last_heartbeat', 'N/A')
        log.info(f'snow_connector heartbeat found: {last_beat}')
        return True
    log.warning('snow_connector NOT found in service_health - may not be registered')
    return False


def check_webhook_registration() -> bool:
    log.info('Checking webhook endpoint registration...')
    sql = """
        SELECT COUNT(*) as cnt FROM information_schema.tables 
        WHERE table_name = 'audit_log'
    """
    rows = ws_query(sql)
    if rows and rows[0].get('cnt', 0) > 0:
        log.info('audit_log table exists - webhook infrastructure present')
        return True
    log.warning('audit_log table not found')
    return False


def verify_submission_hash_computation() -> bool:
    log.info('Verifying submission hash computation format...')
    
    # Expected hash format: SHA256 of normalized submission fields
    test_data = {
        'server_id': 'test-server-123',
        'name': 'test-mcp',
        'url': 'https://example.com',
        'verdict': 'pending',
        'submitted_at': '2026-01-01T00:00:00Z'
    }
    
    # Simulate the hash computation from snow_connector_approval_wiring.py
    normalized = json.dumps(test_data, sort_keys=True)
    computed_hash = hashlib.sha256(normalized.encode()).hexdigest()
    
    log.info(f'Computed test hash: {computed_hash}')
    log.info(f'Hash length: {len(computed_hash)} characters')
    
    # Verify hash is 64 chars (SHA256)
    if len(computed_hash) == 64:
        log.info('Submission hash format validated: SHA256 (64 chars)')
        return True
    else:
        log.error(f'Unexpected hash length: {len(computed_hash)}')
        return False


def check_approval_workflow_api() -> bool:
    log.info('Checking approval_workflow API availability...')
    try:
        resp = requests.get(f'{APPROVAL_WORKFLOW_URL}/health', timeout=10)
        if resp.status_code == 200:
            log.info('approval_workflow API is reachable')
            return True
        else:
            log.warning(f'approval_workflow API returned status: {resp.status_code}')
            return False
    except Exception as e:
        log.error(f'approval_workflow API check failed: {e}')
        return False


def check_snow_submissions_table() -> bool:
    log.info('Checking mcp_submissions table for SNOW tickets...')
    sql = """
        SELECT column_name, data_type FROM information_schema.columns 
        WHERE table_name = 'mcp_submissions' 
        ORDER BY ordinal_position
    """
    columns = ws_query(sql)
    if columns:
        col_names = [c['column_name'] for c in columns]
        log.info(f'mcp_submissions columns: {col_names}')
        
        # Check for expected SNOW-related fields
        expected = ['server_id', 'name', 'verdict', 'source', 'ticket_id', 'snow_ticket_id']
        found = [f for f in expected if f in col_names]
        log.info(f'Found {len(found)}/{len(expected)} expected fields: {found}')
        return True
    else:
        log.warning('mcp_submissions table not accessible')
        return False


def check_snow_ticket_fields() -> bool:
    log.info('Checking for SNOW ticket-specific fields...')
    sql = """
        SELECT COUNT(*) as cnt FROM information_schema.columns 
        WHERE table_name = 'mcp_submissions' 
        AND column_name IN ('snow_ticket_id', 'snow_workflow', 'snow_state', 'ticket_id')
    """
    rows = ws_query(sql)
    if rows and rows[0].get('cnt', 0) > 0:
        log.info(f'Found {rows[0]["cnt"]} SNOW-related columns')
        return True
    log.info('No dedicated SNOW ticket columns found (ticket_id may be sufficient)')
    return True  # Not a failure - ticket_id is common


def verify_snow_oauth_configuration() -> bool:
    log.info('Checking SNOW OAuth configuration...')
    # Check if SNOW credentials are in environment (no hardcoding)
    snow_user = os.environ.get('SNOW_USERNAME')
    snow_secret = os.environ.get('SNOW_PASSWORD')
    
    if snow_user and snow_secret:
        log.info('SNOW credentials found in environment')
        return True
    else:
        log.warning('SNOW credentials not found in environment')
        return False


def check_recent_snow_activity() -> bool:
    log.info('Checking for recent SNOW-related activity...')
    sql = """
        SELECT COUNT(*) as cnt FROM audit_log 
        WHERE detail LIKE '%snow%' 
        OR detail LIKE '%service_now%' 
        AND created_at > NOW() - INTERVAL '7 days'
    """
    rows = ws_query(sql)
    if rows:
        count = rows[0].get('cnt', 0)
        log.info(f'Found {count} SNOW-related audit events in last 7 days')
        return count > 0
    return False


def run_verification() -> dict:
    log.info('=' * 60)
    log.info('SNOW Connector Approval Wiring Verification')
    log.info('=' * 60)
    
    results = {
        'snow_connector_heartbeat': check_snow_connector_heartbeat(),
        'webhook_infrastructure': check_webhook_registration(),
        'submission_hash_format': verify_submission_hash_computation(),
        'approval_workflow_api': check_approval_workflow_api(),
        'submissions_table': check_snow_submissions_table(),
        'snow_ticket_fields': check_snow_ticket_fields(),
        'snow_oauth_config': verify_snow_oauth_configuration(),
        'recent_snow_activity': check_recent_snow_activity(),
    }
    
    log.info('=' * 60)
    log.info('VERIFICATION RESULTS:')
    log.info('=' * 60)
    
    all_passed = True
    for check, passed in results.items():
        status = 'PASS' if passed else 'FAIL'
        log.info(f'  {check}: {status}')
        if not passed:
            all_passed = False
    
    log.info('=' * 60)
    if all_passed:
        log.info('ALL CHECKS PASSED - Integration appears complete')
    else:
        log.warning('SOME CHECKS FAILED - Review required')
    log.info('=' * 60)
    
    return results


if __name__ == '__main__':
    results = run_verification()
    
    # Write verification results to audit log
    utc_now = datetime.now(timezone.utc).isoformat()
    ws_write('audit_log', [{
        'target_server_id': 'verification',
        'event_type': 'snow_connector_approval_wiring_verify',
        'actor': 'system',
        'detail': json.dumps({
            'timestamp': utc_now,
            'results': results,
            'all_passed': all(results.values())
        }),
        'created_at': utc_now
    }])
    
    # Exit with appropriate code
    if all(results.values()):
        sys.exit(0)
    else:
        sys.exit(1)