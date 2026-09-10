import logging
import requests
import sys
import json
from datetime import datetime, timezone

SERVICE_NAME = 'smoke_import_diagnostic'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(SNAME := SERVICE_NAME)

MCP_SENTINEL = '/home/workspace/zo_sentinel'
FAILURE_TARGETS = [
    'registry_api.py',
    'rug_pull_monitor.py', 
    'signal_analyser.py'
]

def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json=payload,
        timeout=15
    )
    resp.raise_for_status()
    return resp.json().get('rows', [])

def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json=payload,
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()

def diagnose():
    findings = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'targets': FAILURE_TARGETS,
        'diagnostics': []
    }
    
    logger.info(f"Diagnosing smoke failures for: {FAILURE_TARGETS}")
    
    # Query service_health for recent errors
    health_sql = """
    SELECT service_name, status, last_heartbeat, error_message, meta
    FROM service_health 
    WHERE service_name IN (
        'registry_api', 'rug_pull_monitor', 'signal_analyser'
    )
    ORDER BY last_heartbeat DESC
    LIMIT 30
    """
    
    try:
        health_rows = ws_query(health_sql)
        logger.info(f"Found {len(health_rows)} health records")
    except Exception as e:
        logger.error(f"service_health query failed: {e}")
        health_rows = []
    
    # Query smoke_test_results for actual failures
    smoke_sql = """
    SELECT test_name, status, error_message, stdout_tail, stderr_tail, 
           created_at, duration_ms
    FROM smoke_test_results
    WHERE test_name LIKE '%registry_api%' 
       OR test_name LIKE '%rug_pull_monitor%'
       OR test_name LIKE '%signal_analyser%'
    ORDER BY created_at DESC
    LIMIT 30
    """
    
    try:
        smoke_rows = ws_query(smoke_sql)
        logger.info(f"Found {len(smoke_rows)} smoke test records")
    except Exception as e:
        logger.error(f"smoke_test_results query failed: {e}")
        smoke_rows = []
    
    # Analyze error patterns
    importlib_patterns = []
    for row in smoke_rows:
        if row.get('error_message') and 'importlib' in str(row.get('error_message', '')):
            importlib_patterns.append({
                'test': row.get('test_name'),
                'error': row.get('error_message'),
                'stderr': row.get('stderr_tail', '')[:500]
            })
    
    # Categorize failures
    categories = {
        'python_path': 0,
        'missing_dependency': 0,
        'circular_import': 0,
        'unknown': 0
    }
    
    for pattern in importlib_patterns:
        err = pattern.get('error', '').lower()
        if 'modulenotfounderror' in err or 'no module named' in err:
            categories['missing_dependency'] += 1
        elif 'circular' in err or 'cycle' in err:
            categories['circular_import'] += 1
        elif 'path' in err or 'sys.path' in err:
            categories['python_path'] += 1
        else:
            categories['unknown'] += 1
    
    # Check for common Python path issues
    path_checks = {}
    try:
        import importlib.util
        path_checks['importlib_available'] = True
    except Exception as e:
        path_checks['importlib_available'] = str(e)
    
    try:
        import sys
        path_checks['sys_path_sample'] = sys.path[:5]
    except Exception as e:
        path_checks['sys_path_error'] = str(e)
    
    # Verify target files exist
    import os
    file_checks = {}
    for target in FAILURE_TARGETS:
        filepath = os.path.join(MCP_SENTINEL, target)
        exists = os.path.exists(filepath)
        file_checks[target] = {
            'path': filepath,
            'exists': exists,
            'size': os.path.getsize(filepath) if exists else None
        }
    
    # Build diagnostic report
    diagnostic = {
        'health_records_found': len(health_rows),
        'smoke_records_found': len(smoke_rows),
        'importlib_errors': len(importlib_patterns),
        'category_breakdown': categories,
        'importlib_patterns': importlib_patterns[:5],
        'file_checks': file_checks,
        'path_checks': path_checks,
        'recommendation': None
    }
    
    # Generate recommendation based on pattern
    if categories['missing_dependency'] >= 2:
        diagnostic['recommendation'] = 'MISSING_DEPENDENCY: Likely missing import in target files. Check sys.path and try: pip install <missing> in builder.'
    elif categories['circular_import'] >= 2:
        diagnostic['recommendation'] = 'CIRCULAR_IMPORT: Check for cyclic imports between target modules. Trace import chain with -v flag.'
    elif categories['python_path'] >= 2:
        diagnostic['recommendation'] = 'PYTHON_PATH: Target files not in sys.path. Ensure MCP_SENTINEL is in PYTHONPATH or service runs from correct cwd.'
    else:
        diagnostic['recommendation'] = 'UNKNOWN_PATTERN: Manual inspection required. Check stderr_tail in smoke_test_results for full traceback.'
    
    findings['diagnostics'].append(diagnostic)
    
    logger.info(f"Diagnosis complete: {diagnostic['recommendation']}")
    
    # Write results to diagnostic table
    ws_write('smoke_diagnostic_results', [{
        'ts': findings['ts'],
        'targets': json.dumps(FAILURE_TARGETS),
        'importlib_errors': len(importlib_patterns),
        'category_breakdown': json.dumps(categories),
        'recommendation': diagnostic['recommendation'],
        'diagnostic_data': json.dumps(diagnostic)
    }])
    
    # Print summary to stdout for smoke test
    print(f"=== SMOKE IMPORT DIAGNOSTIC ===")
    print(f"Targets: {FAILURE_TARGETS}")
    print(f"Importlib errors found: {len(importlib_patterns)}")
    print(f"Category breakdown: {categories}")
    print(f"Recommendation: {diagnostic['recommendation']}")
    print(f"Files checked: {list(file_checks.keys())}")
    for fname, fcheck in file_checks.items():
        status = "EXISTS" if fcheck['exists'] else "MISSING"
        print(f"  {fname}: {status} ({fcheck.get('size', 'N/A')} bytes)")
    
    return 0

if __name__ == '__main__':
    try:
        code = diagnose()
        sys.exit(code)
    except Exception as e:
        logger.error(f"Diagnostic failed: {e}")
        print(f"DIAGNOSTIC ERROR: {e}")
        sys.exit(1)