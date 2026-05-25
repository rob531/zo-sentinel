import requests
import logging
import time
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'

def ws_write(table, rows):
    r = requests.post(WRITE_SERVICE_URL + '/write',
        json={'table': table, 'rows': rows, 'wait': True}, timeout=8)
    return r.status_code == 200

def heartbeat():
    ws_write('service_health', {
        'service': 'diagnose_smoke_failures_20260424',
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    })

def check_import(module_name):
    try:
        __import__(module_name)
        return f"Import successful for {module_name}"
    except ImportError as e:
        return f"Import failed for {module_name}: {str(e)}"

def check_circular_dependency(module1, module2):
    try:
        __import__(module1)
        __import__(module2)
        return f"No circular dependency detected between {module1} and {module2}"
    except ImportError as e:
        return f"Circular dependency or import error: {str(e)}"

def verify_dependencies(module_name, expected_deps):
    actual_deps = set()
    try:
        module = __import__(module_name)
        for attr in dir(module):
            if isinstance(getattr(module, attr), type(__import__('types').ModuleType)):
                actual_deps.add(attr)
    except ImportError as e:
        return f"Failed to verify dependencies for {module_name}: {str(e)}"
    
    missing_deps = expected_deps - actual_deps
    extra_deps = actual_deps - expected_deps
    
    if not missing_deps and not extra_deps:
        return f"All dependencies verified for {module_name}"
    else:
        return (f"Mismatch in dependencies for {module_name}. "
                f"Missing: {missing_deps}, Extra: {extra_deps}")

def run():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('diagnose_smoke_failures_20260424')
    log.info('Starting...')
    heartbeat()
    
    while True:
        try:
            heartbeat()
            
            modules_to_check = ['registry_api', 'rug_pull_monitor', 'signal_analyser']
            for module in modules_to_check:
                log.info(check_import(module))
            
            # Check circular dependencies
            log.info(check_circular_dependency('registry_api', 'rug_pull_monitor'))
            log.info(check_circular_dependency('registry_api', 'signal_analyser'))
            log.info(check_circular_dependency('rug_pull_monitor', 'signal_analyser'))
            
            # Verify dependencies (example: assume these are the expected deps)
            expected_deps_registry = {'os', 'sys'}
            expected_deps_rug_pull = {'requests', 'json'}
            expected_deps_signal = {'numpy', 'pandas'}
            
            log.info(verify_dependencies('registry_api', expected_deps_registry))
            log.info(verify_dependencies('rug_pull_monitor', expected_deps_rug_pull))
            log.info(verify_dependencies('signal_analyser', expected_deps_signal))
        
        except Exception as e:
            log.error('Cycle error: %s', e)
        
        time.sleep(3600)

if __name__ == '__main__':
    run()