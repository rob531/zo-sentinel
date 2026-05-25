import sys
import os
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional

import requests

SERVICE_NAME = 'permission_scope_integration_check'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: Optional[tuple] = None) -> list:
    payload = {'sql': sql}
    if params:
        payload['params'] = list(params)
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', [])


def ws_write(table: str, rows: list) -> None:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()


def ws_execute(sql: str, params: Optional[tuple] = None) -> None:
    payload = {'sql': sql}
    if params:
        payload['params'] = list(params)
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/execute',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()


def check_signal_analyser_imports(file_path: str) -> dict:
    findings = {
        'file_exists': False,
        'imports_permission_scope': False,
        'calls_compute_score': False,
        'writes_mcp_signal_enrichments': False,
        'signal_type_permission_scope': False,
        'content_hash': None
    }
    
    if not os.path.exists(file_path):
        logger.warning(f'signal_analyser.py not found at {file_path}')
        return findings
    
    findings['file_exists'] = True
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    findings['content_hash'] = hashlib.sha256(content.encode()).hexdigest()[:16]
    
    if 'from permission_scope_enrichment import' in content or 'import permission_scope_enrichment' in content:
        findings['imports_permission_scope'] = True
        logger.info('✓ signal_analyser imports permission_scope_enrichment')
    
    if 'compute_score' in content and ('permission_scope' in content.lower() or 'compute_score(' in content):
        findings['calls_compute_score'] = True
        logger.info('✓ signal_analyser calls compute_score')
    
    if 'mcp_signal_enrichments' in content:
        findings['writes_mcp_signal_enrichments'] = True
        logger.info('✓ signal_analyser writes to mcp_signal_enrichments')
        
        import re
        if re.search(r"signal_type\s*=\s*['\"]permission_scope['\"]", content, re.IGNORECASE):
            findings['signal_type_permission_scope'] = True
            logger.info('✓ signal_analyser writes with signal_type=permission_scope')
    
    return findings


def check_mcp_signal_enrichments_table() -> dict:
    findings = {
        'table_exists': False,
        'permission_scope_count': 0,
        'recent_entries': []
    }
    
    check_table_sql = """
    SELECT count(*) as cnt 
    FROM information_schema.tables 
    WHERE table_name = 'mcp_signal_enrichments'
    """
    
    try:
        rows = ws_query(check_table_sql)
        if rows and rows[0].get('cnt', 0) > 0:
            findings['table_exists'] = True
            logger.info('✓ mcp_signal_enrichments table exists')
        else:
            logger.warning('mcp_signal_enrichments table not found')
            return findings
    except Exception as e:
        logger.error(f'Error checking table existence: {e}')
        return findings
    
    recent_count_sql = """
    SELECT count(*) as cnt 
    FROM mcp_signal_enrichments 
    WHERE signal_type = 'permission_scope'
    """
    
    try:
        rows = ws_query(recent_count_sql)
        if rows:
            findings['permission_scope_count'] = rows[0].get('cnt', 0)
            logger.info(f'Found {findings["permission_scope_count"]} permission_scope enrichment records')
    except Exception as e:
        logger.error(f'Error querying permission_scope count: {e}')
    
    recent_entries_sql = """
    SELECT target_server_id, score, computed_at 
    FROM mcp_signal_enrichments 
    WHERE signal_type = 'permission_scope'
    ORDER BY computed_at DESC 
    LIMIT 10
    """
    
    try:
        findings['recent_entries'] = ws_query(recent_entries_sql)
        if findings['recent_entries']:
            logger.info(f'Retrieved {len(findings["recent_entries"])} recent permission_scope entries')
    except Exception as e:
        logger.error(f'Error fetching recent entries: {e}')
    
    return findings


def check_permission_scope_enrichment_file() -> dict:
    findings = {
        'file_exists': False,
        'has_compute_score_function': False,
        'content_hash': None
    }
    
    possible_paths = [
        '/home/workspace/zo_sentinel/permission_scope_enrichment.py',
        '/home/workspace/zo_sentinel/permission_scope.py',
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            findings['file_exists'] = True
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            findings['content_hash'] = hashlib.sha256(content.encode()).hexdigest()[:16]
            
            if 'def compute_score' in content:
                findings['has_compute_score_function'] = True
                logger.info(f'✓ {path} has compute_score function')
            break
    
    return findings


def run_audit() -> dict:
    audit_ts = datetime.now(timezone.utc).isoformat()
    
    logger.info('=' * 60)
    logger.info('Starting Permission Scope Integration Audit')
    logger.info('=' * 60)
    
    signal_analyser_path = '/home/workspace/zo_sentinel/signal_analyser.py'
    analyser_findings = check_signal_analyser_imports(signal_analyser_path)
    
    enrichment_findings = check_permission_scope_enrichment_file()
    
    table_findings = check_mcp_signal_enrichments_table()
    
    all_checks = [
        ('signal_analyser_imports_ps', analyser_findings['imports_permission_scope']),
        ('signal_analyser_calls_compute_score', analyser_findings['calls_compute_score']),
        ('signal_analyser_writes_enrichments', analyser_findings['writes_mcp_signal_enrichments']),
        ('signal_analyser_uses_ps_signal_type', analyser_findings['signal_type_permission_scope']),
        ('ps_table_exists', table_findings['table_exists']),
        ('ps_table_has_records', table_findings['permission_scope_count'] > 0),
    ]
    
    passed = sum(1 for _, v in all_checks if v)
    total = len(all_checks)
    
    logger.info('-' * 60)
    logger.info(f'Integration Audit Summary: {passed}/{total} checks passed')
    
    for check_name, result in all_checks:
        status = 'PASS' if result else 'FAIL'
        logger.info(f'  [{status}] {check_name}')
    
    if table_findings['permission_scope_count'] > 0:
        logger.info(f'  Records in mcp_signal_enrichments: {table_findings["permission_scope_count"]}')
    
    logger.info('=' * 60)
    
    audit_record = {
        'audit_timestamp': audit_ts,
        'service_name': SERVICE_NAME,
        'signal_analyser_file_exists': analyser_findings['file_exists'],
        'signal_analyser_content_hash': analyser_findings['content_hash'],
        'imports_permission_scope': analyser_findings['imports_permission_scope'],
        'calls_compute_score': analyser_findings['calls_compute_score'],
        'writes_mcp_signal_enrichments': analyser_findings['writes_mcp_signal_enrichments'],
        'uses_permission_scope_signal_type': analyser_findings['signal_type_permission_scope'],
        'permission_scope_enrichment_file_exists': enrichment_findings['file_exists'],
        'has_compute_score_function': enrichment_findings['has_compute_score_function'],
        'mcp_signal_enrichments_table_exists': table_findings['table_exists'],
        'permission_scope_record_count': table_findings['permission_scope_count'],
        'checks_passed': passed,
        'checks_total': total,
        'overall_status': 'PASS' if passed == total else 'FAIL'
    }
    
    try:
        ws_write('integration_audit_log', [audit_record])
        logger.info('Audit record written to integration_audit_log')
    except Exception as e:
        logger.error(f'Failed to write audit record: {e}')
    
    return audit_record


if __name__ == '__main__':
    result = run_audit()
    
    if result['overall_status'] == 'PASS':
        logger.info('Audit completed successfully')
        sys.exit(0)
    else:
        logger.warning(f'Audit completed with failures: {result["checks_passed"]}/{result["checks_total"]} passed')
        sys.exit(1)