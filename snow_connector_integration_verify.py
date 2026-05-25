import sys
sys.path.insert(0, '/home/workspace')

import requests
import re
import os
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'snow_connector_integration_verify'
LOG_FILE = '/home/workspace/logs/snow_connector_integration_verify.log'

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SNOW_CONNECTOR_PATH = '/home/workspace/zo_sentinel/snow_connector.py'
APPROVAL_WORKFLOW_PATH = '/home/workspace/zo_sentinel/approval_workflow.py'


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service for SELECT operations."""
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write to write_service."""
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return False


def send_heartbeat() -> None:
    """Send heartbeat to service_health."""
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    }])


def read_source_file(filepath: str) -> Optional[str]:
    """Read source file content."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return None


def check_snow_connector_wiring(source: str) -> Dict[str, Any]:
    """Verify snow_connector.py uses ws_query/ws_write correctly."""
    findings = {
        'uses_ws_query': False,
        'uses_ws_write': False,
        'direct_duckdb_usage': False,
        'missing_ws_calls': [],
        'ws_query_locations': [],
        'ws_write_locations': [],
        'direct_db_locations': []
    }
    
    # Check for ws_query usage
    ws_query_patterns = [
        r'\bws_query\s*\(',
        r'ws_query\s*\(',
        r'requests\.post.*\/query'
    ]
    for pattern in ws_query_patterns:
        matches = re.findall(pattern, source)
        if matches:
            findings['uses_ws_query'] = True
            for m in matches:
                line_num = source[:source.find(m)].count('\n') + 1
                findings['ws_query_locations'].append(f"line {line_num}: {m[:50]}")
    
    # Check for ws_write usage
    ws_write_patterns = [
        r'\bws_write\s*\(',
        r'ws_write\s*\(',
        r'requests\.post.*\/write'
    ]
    for pattern in ws_write_patterns:
        matches = re.findall(pattern, source)
        if matches:
            findings['uses_ws_write'] = True
            for m in matches:
                line_num = source[:source.find(m)].count('\n') + 1
                findings['ws_write_locations'].append(f"line {line_num}: {m[:50]}")
    
    # Check for direct duckdb usage (BAD)
    direct_db_patterns = [
        r'import\s+duckdb',
        r'from\s+duckdb',
        r'duckdb\.connect\s*\(',
        r'\.connect\s*\(\s*[\'"]',
        r'sqlite3\.connect'
    ]
    for pattern in direct_db_patterns:
        matches = re.findall(pattern, source)
        if matches:
            findings['direct_duckdb_usage'] = True
            for m in matches:
                line_num = source[:source.find(m)].count('\n') + 1
                findings['direct_db_locations'].append(f"line {line_num}: {m}")
    
    # Identify table operations that might be missing ws calls
    table_ops = re.findall(r'(?:INSERT|UPDATE|DELETE|SELECT).*?(?:INTO|FROM|TO)\s+(\w+)', source, re.IGNORECASE)
    relevant_tables = ['mcp_submissions', 'approval_queue', 'service_health', 'audit_log']
    for table in table_ops:
        if table in relevant_tables:
            # Check if this operation is wrapped in ws_query/ws_write
            context_pattern = rf'(ws_query|ws_write).*{table}'
            if not re.search(context_pattern, source, re.IGNORECASE | re.DOTALL):
                findings['missing_ws_calls'].append(f"Table '{table}' has SQL operation but may lack ws wrapper")
    
    return findings


def check_approval_workflow_wiring(source: str) -> Dict[str, Any]:
    """Verify approval_workflow.py calls snow_connector endpoint correctly."""
    findings = {
        'has_snow_endpoint_call': False,
        'uses_write_service_pattern': False,
        'webhook_registration': False,
        'snow_connector_import': False,
        'endpoint_call_locations': [],
        'webhook_locations': [],
        'issues': []
    }
    
    # Check for snow_connector import/call
    import_patterns = [
        r'import\s+snow_connector',
        r'from\s+snow_connector',
        r'snow_connector\.',
        r'requests\.post.*snow',
        r'requests\.post.*8776'
    ]
    for pattern in import_patterns:
        matches = re.findall(pattern, source, re.IGNORECASE)
        if matches:
            findings['snow_connector_import'] = True
            for m in matches:
                line_num = source[:source.find(m)].count('\n') + 1
                findings['endpoint_call_locations'].append(f"line {line_num}: {m[:60]}")
    
    # Check for write_service pattern usage
    write_service_patterns = [
        r'requests\.post.*8772',
        r'WRITE_SERVICE_URL',
        r'ws_write',
        r'ws_query'
    ]
    for pattern in write_service_patterns:
        if re.search(pattern, source):
            findings['uses_write_service_pattern'] = True
            break
    
    # Check for webhook/Servicenow ticket handling
    webhook_patterns = [
        r'webhook',
        r'servicenow',
        r'@app\.post.*ticket',
        r'@app\.post.*snow',
        r'mcp_submissions'
    ]
    for pattern in webhook_patterns:
        matches = re.findall(pattern, source, re.IGNORECASE)
        if matches:
            findings['webhook_registration'] = True
            for m in matches:
                line_num = source[:source.find(m)].count('\n') + 1
                findings['webhook_locations'].append(f"line {line_num}: {m[:60]}")
    
    return findings


def check_mcp_submissions_ingestion() -> Dict[str, Any]:
    """Query write_service for recent mcp_submissions to verify ServiceNow ticket ingestion."""
    findings = {
        'total_submissions': 0,
        'servicenow_tickets': 0,
        'recent_tickets': [],
        'submission_sources': {},
        'has_recent_ingestion': False,
        'last_submission_time': None,
        'issues': []
    }
    
    # Query recent submissions
    sql = """
        SELECT id, server_id, source, submission_type, status, created_at, metadata
        FROM mcp_submissions
        ORDER BY created_at DESC
        LIMIT 50
    """
    rows = ws_query(sql)
    findings['total_submissions'] = len(rows)
    
    # Analyze submission sources and types
    for row in rows:
        source = row.get('source', 'unknown')
        submission_type = row.get('submission_type', 'unknown')
        metadata = row.get('metadata', '{}')
        
        findings['submission_sources'][source] = findings['submission_sources'].get(source, 0) + 1
        
        # Check for ServiceNow tickets
        if 'snow' in source.lower() or 'servicenow' in source.lower() or 'snow' in str(metadata).lower():
            findings['servicenow_tickets'] += 1
            findings['recent_tickets'].append({
                'id': row.get('id'),
                'created_at': row.get('created_at'),
                'source': source
            })
        
        if submission_type == 'servicenow' or submission_type == 'snow':
            findings['servicenow_tickets'] += 1
    
    # Check for recent activity (last 24 hours)
    if rows:
        latest_created = rows[0].get('created_at', '')
        if latest_created:
            try:
                from datetime import timedelta
                latest_dt = datetime.fromisoformat(latest_created.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                findings['last_submission_time'] = latest_created
                if (now - latest_dt.replace(tzinfo=None)).total_seconds() < 86400:
                    findings['has_recent_ingestion'] = True
            except Exception as e:
                findings['issues'].append(f"Could not parse last submission time: {e}")
    
    if findings['total_submissions'] == 0:
        findings['issues'].append("No mcp_submissions found in database - ServiceNow tickets may not be ingested")
    
    return findings


def check_endpoint_wiring() -> Dict[str, Any]:
    """Verify snow_connector endpoint is properly wired in the system."""
    findings = {
        'snow_connector_port': 8776,
        'endpoint_registered': False,
        'approval_queue_integration': False,
        'ticket_states_handled': [],
        'issues': []
    }
    
    # Check what ports are active (basic check)
    try:
        resp = requests.get(f'http://localhost:8776/health', timeout=5)
        if resp.status_code == 200:
            findings['endpoint_registered'] = True
    except:
        findings['issues'].append("snow_connector endpoint (port 8776) not responding")
    
    # Check if approval_workflow connects to snow connector
    workflow_source = read_source_file(APPROVAL_WORKFLOW_PATH)
    if workflow_source:
        if re.search(r'8776', workflow_source) or re.search(r'snow_connector', workflow_source):
            findings['approval_queue_integration'] = True
        
        # Check for ticket state handling
        state_patterns = re.findall(r'(?:status|state|verdict)\s*[=:]\s*[\'"]([\w_]+)[\'"]', workflow_source, re.IGNORECASE)
        for state in state_patterns:
            if state.lower() not in ['none', 'null']:
                findings['ticket_states_handled'].append(state)
    
    return findings


def generate_wiring_report(
    snow_connector_findings: Dict,
    approval_workflow_findings: Dict,
    submissions_findings: Dict,
    endpoint_findings: Dict
) -> str:
    """Generate a comprehensive wiring completeness report."""
    
    report_lines = []
    report_lines.append("=" * 70)
    report_lines.append("SNOW CONNECTOR INTEGRATION VERIFICATION REPORT")
    report_lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    report_lines.append("=" * 70)
    
    # Section 1: snow_connector.py wiring
    report_lines.append("\n" + "=" * 70)
    report_lines.append("SECTION 1: snow_connector.py WIRING ANALYSIS")
    report_lines.append("=" * 70)
    
    if snow_connector_findings['direct_duckdb_usage']:
        report_lines.append("\n[FAIL] Direct duckdb/sqlite3 usage detected!")
        for loc in snow_connector_findings['direct_db_locations']:
            report_lines.append(f"  - {loc}")
        report_lines.append("\n  ACTION REQUIRED: Replace direct DB calls with ws_query/ws_write")
    else:
        report_lines.append("\n[PASS] No direct duckdb/sqlite3 usage detected")
    
    report_lines.append(f"\nws_query usage: {'FOUND' if snow_connector_findings['uses_ws_query'] else 'MISSING'}")
    for loc in snow_connector_findings['ws_query_locations']:
        report_lines.append(f"  + {loc}")
    
    report_lines.append(f"\nws_write usage: {'FOUND' if snow_connector_findings['uses_ws_write'] else 'MISSING'}")
    for loc in snow_connector_findings['ws_write_locations']:
        report_lines.append(f"  + {loc}")
    
    if snow_connector_findings['missing_ws_calls']:
        report_lines.append("\n[FAIL] Potentially unwrapped table operations:")
        for issue in snow_connector_findings['missing_ws_calls']:
            report_lines.append(f"  - {issue}")
    
    # Section 2: approval_workflow.py wiring
    report_lines.append("\n" + "=" * 70)
    report_lines.append("SECTION 2: approval_workflow.py WIRING ANALYSIS")
    report_lines.append("=" * 70)
    
    report_lines.append(f"\nsnow_connector import/call: {'FOUND' if approval_workflow_findings['snow_connector_import'] else 'MISSING'}")
    for loc in approval_workflow_findings['endpoint_call_locations']:
        report_lines.append(f"  + {loc}")
    
    report_lines.append(f"\nwrite_service pattern usage: {'FOUND' if approval_workflow_findings['uses_write_service_pattern'] else 'MISSING'}")
    
    report_lines.append(f"\nwebhook/mcp_submissions registration: {'FOUND' if approval_workflow_findings['webhook_registration'] else 'MISSING'}")
    for loc in approval_workflow_findings['webhook_locations']:
        report_lines.append(f"  + {loc}")
    
    if not approval_workflow_findings['snow_connector_import']:
        report_lines.append("\n[FAIL] approval_workflow.py does not import or call snow_connector!")
        report_lines.append("  ACTION REQUIRED: Add integration to route ServiceNow tickets appropriately")
    
    # Section 3: ServiceNow ticket ingestion
    report_lines.append("\n" + "=" * 70)
    report_lines.append("SECTION 3: SERVICENOW TICKET INGESTION VERIFICATION")
    report_lines.append("=" * 70)
    
    report_lines.append(f"\nTotal mcp_submissions rows: {submissions_findings['total_submissions']}")
    report_lines.append(f"ServiceNow tickets detected: {submissions_findings['servicenow_tickets']}")
    report_lines.append(f"Recent ingestion (24h): {'YES' if submissions_findings['has_recent_ingestion'] else 'NO'}")
    if submissions_findings['last_submission_time']:
        report_lines.append(f"Last submission: {submissions_findings['last_submission_time']}")
    
    report_lines.append("\nSubmission sources:")
    for source, count in submissions_findings['submission_sources'].items():
        report_lines.append(f"  - {source}: {count}")
    
    if submissions_findings['issues']:
        report_lines.append("\n[FAIL] Ingestion issues:")
        for issue in submissions_findings['issues']:
            report_lines.append(f"  - {issue}")
    
    # Section 4: Endpoint wiring
    report_lines.append("\n" + "=" * 70)
    report_lines.append("SECTION 4: ENDPOINT WIRING VERIFICATION")
    report_lines.append("=" * 70)
    
    report_lines.append(f"\nsnow_connector endpoint (port 8776): {'REGISTERED' if endpoint_findings['endpoint_registered'] else 'NOT RESPONDING'}")
    report_lines.append(f"approval_queue integration: {'CONNECTED' if endpoint_findings['approval_queue_integration'] else 'NOT CONNECTED'}")
    
    if endpoint_findings['ticket_states_handled']:
        report_lines.append("\nTicket states handled:")
        for state in set(endpoint_findings['ticket_states_handled']):
            report_lines.append(f"  - {state}")
    
    if endpoint_findings['issues']:
        report_lines.append("\n[FAIL] Endpoint issues:")
        for issue in endpoint_findings['issues']:
            report_lines.append(f"  - {issue}")
    
    # Summary
    report_lines.append("\n" + "=" * 70)
    report_lines.append("SUMMARY")
    report_lines.append("=" * 70)
    
    issues_found = []
    if snow_connector_findings['direct_duckdb_usage']:
        issues_found.append("Direct DB usage in snow_connector.py")
    if snow_connector_findings['missing_ws_calls']:
        issues_found.append("Unwrapped table operations in snow_connector.py")
    if not approval_workflow_findings['snow_connector_import']:
        issues_found.append("approval_workflow.py missing snow_connector integration")
    if submissions_findings['total_submissions'] == 0:
        issues_found.append("No mcp_submissions data - ServiceNow ingestion may be broken")
    if not endpoint_findings['approval_queue_integration']:
        issues_found.append("Approval workflow not integrated with snow connector")
    
    if issues_found:
        report_lines.append("\n[GAPS DETECTED]")
        for issue in issues_found:
            report_lines.append(f"  - {issue}")
    else:
        report_lines.append("\n[PASS] All wiring checks passed")
    
    report_lines.append("\n" + "=" * 70)
    
    return '\n'.join(report_lines)


def main() -> int:
    """Run verification checks."""
    logger.info("Starting snow_connector integration verification")
    send_heartbeat()
    
    # Read source files
    snow_connector_source = read_source_file(SNOW_CONNECTOR_PATH)
    approval_workflow_source = read_source_file(APPROVAL_WORKFLOW_PATH)
    
    if not snow_connector_source:
        logger.error(f"Cannot read {SNOW_CONNECTOR_PATH}")
        return 1
    if not approval_workflow_source:
        logger.error(f"Cannot read {APPROVAL_WORKFLOW_PATH}")
        return 1
    
    # Run checks
    snow_connector_findings = check_snow_connector_wiring(snow_connector_source)
    approval_workflow_findings = check_approval_workflow_wiring(approval_workflow_source)
    submissions_findings = check_mcp_submissions_ingestion()
    endpoint_findings = check_endpoint_wiring()
    
    # Generate report
    report = generate_wiring_report(
        snow_connector_findings,
        approval_workflow_findings,
        submissions_findings,
        endpoint_findings
    )
    
    # Print report
    print(report)
    
    # Log report to file
    logger.info("Wiring verification complete")
    send_heartbeat()
    
    return 0


if __name__ == '__main__':
    sys.exit(main())