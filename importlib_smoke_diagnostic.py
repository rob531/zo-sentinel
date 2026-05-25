import logging
import os
import sys
import traceback

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/importlib_smoke_diagnostic.log')]
)

logger = logging.getLogger(__name__)

SERVICE_NAME = 'importlib_smoke_diagnostic'
WRITE_SERVICE_URL = 'http://localhost:8772'


def ws_write(table, rows):
    """Write to DuckDB via write_service HTTP."""
    import requests
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def diagnose_import_failure(module_path):
    """Diagnose why a specific module fails to import."""
    results = {
        'module_path': module_path,
        'success': False,
        'error_type': None,
        'error_message': None,
        'traceback_lines': [],
        'suspected_frozen_import': False,
        'suspected_missing_dependency': False,
        'suspected_syntax_error': False
    }
    
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("diagnostic_target", module_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules['diagnostic_target'] = module
            spec.loader.exec_module(module)
            results['success'] = True
    except SyntaxError as e:
        results['error_type'] = 'SyntaxError'
        results['error_message'] = str(e)
        results['suspected_syntax_error'] = True
        results['traceback_lines'] = traceback.format_exc().split('\n')
    except ImportError as e:
        results['error_type'] = 'ImportError'
        results['error_message'] = str(e)
        results['suspected_missing_dependency'] = True
        results['traceback_lines'] = traceback.format_exc().split('\n')
        # Check for frozen import marker in traceback
        tb_text = traceback.format_exc()
        if '<frozen importlib' in tb_text or 'line 10' in tb_text:
            results['suspected_frozen_import'] = True
    except Exception as e:
        results['error_type'] = type(e).__name__
        results['error_message'] = str(e)
        results['traceback_lines'] = traceback.format_exc().split('\n')
    
    return results


def check_zo_sentinel_modules():
    """Check all known ZO-Sentinel modules for import issues."""
    sentinel_dir = '/home/workspace/zo_sentinel'
    modules_to_check = [
        'registry_api.py',
        'rug_pull_monitor.py', 
        'signal_analyser.py',
        'threat_intel_ingestor.py'
    ]
    
    findings = []
    
    for module_name in modules_to_check:
        module_path = os.path.join(sentinel_dir, module_name)
        if os.path.exists(module_path):
            logger.info(f"Checking import: {module_path}")
            result = diagnose_import_failure(module_path)
            findings.append({
                'module_name': module_name,
                'module_path': module_path,
                'import_result': result
            })
            if result['success']:
                logger.info(f"  OK: {module_name}")
            else:
                logger.warning(f"  FAIL: {module_name} - {result['error_type']}: {result['error_message']}")
                if result['suspected_frozen_import']:
                    logger.warning(f"  -> Suspected frozen importlib issue")
                if result['suspected_missing_dependency']:
                    logger.warning(f"  -> Suspected missing dependency")
        else:
            logger.warning(f"  Module not found: {module_path}")
    
    return findings


def parse_smoke_output_for_import_errors(smoke_output):
    """Parse smoke test output to extract importlib errors."""
    import re
    
    findings = {
        'frozen_import_detected': False,
        'line_10_in_frozen': False,
        'specific_import_failures': [],
        'full_traceback': []
    }
    
    # Check for frozen importlib marker
    if '<frozen importlib' in smoke_output:
        findings['frozen_import_detected'] = True
        
    # Check for "line 10" in frozen context
    if re.search(r'File .<frozen importlib.*>\s+line \d+', smoke_output):
        findings['line_10_in_frozen'] = True
    
    # Extract ImportError lines
    import_error_pattern = r'ImportError.*'
    findings['specific_import_failures'] = re.findall(import_error_pattern, smoke_output)
    
    return findings


def write_diagnostic_findings(findings):
    """Write diagnostic findings to service_health via write_service."""
    import hashlib
    from datetime import datetime, timezone
    
    diagnostic_id = hashlib.md5(
        f"importlib_diagnostic_{datetime.now(timezone.utc).isoformat()}".encode()
    ).hexdigest()[:16]
    
    row = {
        'diagnostic_id': diagnostic_id,
        'service_name': SERVICE_NAME,
        'ts': datetime.now(timezone.utc).isoformat(),
        'modules_checked': len(findings),
        'failures': sum(1 for f in findings if not f.get('import_result', {}).get('success', False)),
        'findings_json': str(findings)
    }
    
    try:
        ws_write('importlib_diagnostic_results', row)
        logger.info(f"Wrote diagnostic findings with id: {diagnostic_id}")
    except Exception as e:
        logger.error(f"Failed to write findings: {e}")


def run():
    """Main diagnostic run."""
    logger.info("=" * 60)
    logger.info(f"{SERVICE_NAME} starting diagnostic run")
    logger.info("=" * 60)
    
    # Check all ZO-Sentinel modules
    findings = check_zo_sentinel_modules()
    
    # Log summary
    success_count = sum(1 for f in findings if f.get('import_result', {}).get('success', False))
    failure_count = len(findings) - success_count
    
    logger.info(f"Import diagnostic complete: {success_count} OK, {failure_count} FAILED")
    
    for f in findings:
        if not f.get('import_result', {}).get('success', False):
            result = f['import_result']
            logger.warning(f"  {f['module_name']}: {result['error_type']} - {result['error_message']}")
            
            if result.get('suspected_frozen_import'):
                logger.warning(f"    -> Frozen importlib issue detected - check __pycache__ or .pyc corruption")
            if result.get('suspected_missing_dependency'):
                logger.warning(f"    -> Missing dependency - run pip list and compare imports")
            if result.get('suspected_syntax_error'):
                logger.warning(f"    -> Syntax error in source - check with python -m py_compile")
    
    # Write findings to DuckDB
    write_diagnostic_findings(findings)
    
    logger.info(f"{SERVICE_NAME} diagnostic run complete")
    return findings


if __name__ == '__main__':
    run()
    sys.exit(0)