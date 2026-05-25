import sys
import traceback
import importlib.util
from datetime import datetime, timezone
import requests
import inspect
import pkgutil
import importlib

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

def get_import_error_details(exc_type, exc_value, tb):
    details = {
        'error_type': exc_type.__name__ if exc_type else None,
        'error_message': str(exc_value),
        'traceback_lines': [],
        'missing_modules': [],
        'circular_imports': [],
        'module_path': None
    }
    
    for line in traceback.format_tb(tb):
        details['traceback_lines'].append(line)
        if 'File "' in line and 'line ' in line:
            parts = line.split('"')
            if len(parts) >= 2:
                details['module_path'] = parts[1]
    
    if exc_value and hasattr(exc_value, 'name'):
        details['missing_modules'].append(str(exc_value.name))
    
    return details

def check_module_exists(module_name):
    spec = importlib.util.find_spec(module_name)
    return spec is not None

def get_registry_api_source_path():
    return "/home/workspace/zo_sentinel/registry_api.py"

def diagnose_import_failure():
    results = {
        'diagnostic_timestamp': datetime.now(timezone.utc).isoformat(),
        'target_file': get_registry_api_source_path(),
        'python_version': sys.version,
        'import_errors': [],
        'missing_dependencies': [],
        'circular_import_detected': False,
        'module_resolution_failures': [],
        'import_chain': [],
        'recommendations': []
    }
    
    try:
        import registry_api
    except ImportError as e:
        tb = sys.exc_info()[2]
        error_details = get_import_error_details(type(e), e, tb)
        results['import_errors'].append(error_details)
        
        results['import_chain'] = error_details['traceback_lines']
        
        if error_details['missing_modules']:
            for mod in error_details['missing_modules']:
                exists = check_module_exists(mod)
                results['missing_dependencies'].append({
                    'module': mod,
                    'exists': exists,
                    'installed_path': importlib.util.find_spec(mod)
                })
        
        for line in error_details['traceback_lines']:
            if '<frozen importlib>' in line:
                results['circular_import_detected'] = True
        
        if results['circular_import_detected']:
            results['recommendations'].append("Circular import detected in import chain")
        
        missing = [d['module'] for d in results['missing_dependencies'] if not d['exists']]
        if missing:
            results['recommendations'].append(f"Install missing modules: {' '.join(missing)}")
        
        sys.path_importer_cache.clear()
    
    results['python311_compatible'] = sys.version_info >= (3, 11)
    
    try:
        spec = importlib.util.spec_from_file_location("registry_api", get_registry_api_source_path())
        if spec and spec.loader:
            results['module_loadable'] = True
            results['module_loader'] = str(type(spec.loader).__name__)
        else:
            results['module_loadable'] = False
            results['recommendations'].append("Module file exists but cannot be loaded as Python module")
    except Exception as ex:
        results['module_loadable'] = False
        results['module_load_error'] = str(ex)
    
    deps_to_check = ['fastapi', 'uvicorn', 'pydantic', 'sqlalchemy', 'duckdb']
    for dep in deps_to_check:
        results['missing_dependencies'].append({
            'module': dep,
            'exists': check_module_exists(dep)
        })
    
    return results

def report_to_service_health(diagnostic_results):
    payload = {
        'table': 'service_health',
        'rows': {
            'service': 'registry_api_smoke_diagnostic',
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': 'failed' if diagnostic_results['import_errors'] else 'passed',
            'diagnostic_blob': str(diagnostic_results)
        },
        'wait': True
    }
    
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False

def run():
    print(f"[DIAGNOSTIC] Starting registry_api smoke failure diagnosis at {datetime.now(timezone.utc).isoformat()}")
    
    diagnostic_results = diagnose_import_failure()
    
    report_to_service_health(diagnostic_results)
    
    print(f"[DIAGNOSTIC] Import errors found: {len(diagnostic_results['import_errors'])}")
    print(f"[DIAGNOSTIC] Missing dependencies: {[d['module'] for d in diagnostic_results['missing_dependencies'] if not d.get('exists', True)]}")
    print(f"[DIAGNOSTIC] Circular import detected: {diagnostic_results['circular_import_detected']}")
    
    if diagnostic_results['import_errors']:
        for err in diagnostic_results['import_errors']:
            print(f"[DIAGNOSTIC] Error: {err['error_type']}: {err['error_message']}")
    
    print(f"[DIAGNOSTIC] Recommendations: {diagnostic_results['recommendations']}")
    
    return diagnostic_results

if __name__ == '__main__':
    run()