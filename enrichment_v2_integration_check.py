import ast
import os
import sys
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

SERVICE_NAME = 'enrichment_v2_integration_check'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)

SIGNAL_ANALYSER_PATH = '/home/workspace/zo_sentinel/signal_analyser.py'
PERMISSION_SCOPE_V2_PATH = '/home/workspace/zo_sentinel/permission_scope_enrichment_v2.py'
TEMPORAL_STABILITY_V2_PATH = '/home/workspace/zo_sentinel/temporal_stability_enrichment_v2.py'


def ws_query(sql: str, params: Optional[tuple] = None) -> list:
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', result.get('data', []))


def check_imports_and_calls(filepath: str, module_name: str, func_name: str) -> dict:
    result = {
        'file_exists': os.path.exists(filepath),
        'imports_module': False,
        'calls_compute_score': False,
        'details': []
    }
    
    if not result['file_exists']:
        result['details'].append(f'{filepath} does not exist')
        return result
    
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == module_name:
                    result['imports_module'] = True
                    result['details'].append(f'Found: from {node.module} import ...')
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == func_name:
                        result['calls_compute_score'] = True
                        result['details'].append(f'Found call: .{func_name}()')
                elif isinstance(node.func, ast.Name):
                    if node.func.id == func_name:
                        result['calls_compute_score'] = True
                        result['details'].append(f'Found call: {func_name}()')
        
    except Exception as e:
        result['details'].append(f'Parse error: {str(e)}')
    
    return result


def check_signal_enrichments(signal_name: str) -> dict:
    result = {
        'signal_name': signal_name,
        'count': 0,
        'has_recent': False,
        'error': None
    }
    
    try:
        sql = """
        SELECT COUNT(*) as cnt, MAX(computed_at) as latest
        FROM mcp_signal_enrichments
        WHERE signal_name = ?
        """
        rows = ws_query(sql, (signal_name,))
        
        if rows:
            result['count'] = rows[0].get('cnt', 0) if isinstance(rows[0], dict) else rows[0][0]
            latest = rows[0].get('latest', rows[0][1] if len(rows[0]) > 1 else None)
            result['has_recent'] = result['count'] > 0
            if latest:
                result['details'] = f'Latest: {latest}'
        else:
            result['count'] = 0
            result['details'] = 'No rows found'
            
    except Exception as e:
        result['error'] = str(e)
        result['details'] = f'Query error: {str(e)}'
    
    return result


def send_heartbeat(status: str, meta: dict):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': ts,
        'meta': str(meta)
    }
    try:
        requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': 'service_health', 'rows': row, 'wait': True},
            timeout=10
        )
    except Exception as e:
        logger.warning(f'Heartbeat failed: {e}')


def main():
    logger.info('Starting enrichment_v2 integration check')
    
    issues = []
    checks_passed = 0
    checks_total = 0
    
    checks_total += 1
    logger.info('Checking signal_analyser.py imports permission_scope_enrichment_v2...')
    perm_result = check_imports_and_calls(
        SIGNAL_ANALYSER_PATH,
        'permission_scope_enrichment_v2',
        'compute_score'
    )
    
    if perm_result['file_exists']:
        checks_total += 1
        if perm_result['imports_module'] and perm_result['calls_compute_score']:
            logger.info(f'  PASS: signal_analyser imports and calls compute_score from permission_scope_enrichment_v2')
            checks_passed += 1
        else:
            logger.warning(f'  FAIL: imports_module={perm_result["imports_module"]}, calls_compute_score={perm_result["calls_compute_score"]}')
            for detail in perm_result['details']:
                logger.warning(f'    {detail}')
            issues.append('signal_analyser does not properly integrate permission_scope_enrichment_v2')
    else:
        logger.error(f'  FAIL: {SIGNAL_ANALYSER_PATH} not found')
        issues.append('signal_analyser.py does not exist')
    
    checks_total += 1
    logger.info('Checking signal_analyser.py imports temporal_stability_enrichment_v2...')
    temp_result = check_imports_and_calls(
        SIGNAL_ANALYSER_PATH,
        'temporal_stability_enrichment_v2',
        'compute_score'
    )
    
    if temp_result['file_exists']:
        checks_total += 1
        if temp_result['imports_module'] and temp_result['calls_compute_score']:
            logger.info(f'  PASS: signal_analyser imports and calls compute_score from temporal_stability_enrichment_v2')
            checks_passed += 1
        else:
            logger.warning(f'  FAIL: imports_module={temp_result["imports_module"]}, calls_compute_score={temp_result["calls_compute_score"]}')
            for detail in temp_result['details']:
                logger.warning(f'    {detail}')
            issues.append('signal_analyser does not properly integrate temporal_stability_enrichment_v2')
    else:
        logger.error(f'  FAIL: {SIGNAL_ANALYSER_PATH} not found')
        issues.append('signal_analyser.py does not exist')
    
    logger.info('Checking mcp_signal_enrichments for permission_scope rows...')
    perm_enrich = check_signal_enrichments('permission_scope')
    checks_total += 1
    if perm_enrich['count'] > 0:
        logger.info(f'  PASS: Found {perm_enrich["count"]} permission_scope enrichment rows. {perm_enrich.get("details", "")}')
        checks_passed += 1
    else:
        logger.warning(f'  FAIL: No permission_scope enrichment rows found')
        if perm_enrich.get('error'):
            logger.warning(f'  Error: {perm_enrich["error"]}')
        issues.append('permission_scope enrichments not flowing into mcp_signal_enrichments')
    
    logger.info('Checking mcp_signal_enrichments for temporal_stability rows...')
    temp_enrich = check_signal_enrichments('temporal_stability')
    checks_total += 1
    if temp_enrich['count'] > 0:
        logger.info(f'  PASS: Found {temp_enrich["count"]} temporal_stability enrichment rows. {temp_enrich.get("details", "")}')
        checks_passed += 1
    else:
        logger.warning(f'  FAIL: No temporal_stability enrichment rows found')
        if temp_enrich.get('error'):
            logger.warning(f'  Error: {temp_enrich["error"]}')
        issues.append('temporal_stability enrichments not flowing into mcp_signal_enrichments')
    
    meta = {
        'checks_passed': checks_passed,
        'checks_total': checks_total,
        'issues': issues
    }
    
    send_heartbeat('completed', meta)
    
    logger.info(f'Integration check complete: {checks_passed}/{checks_total} passed')
    
    if issues:
        logger.warning('ISSUES DETECTED:')
        for issue in issues:
            logger.warning(f'  - {issue}')
        sys.exit(1)
    else:
        logger.info('All enrichment_v2 integration checks passed')
        sys.exit(0)


if __name__ == '__main__':
    main()