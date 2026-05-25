#!/usr/bin/env python3
"""
diagnose_import_traces_rug_registry_signal.py -- ZO-SENTINEL Diagnostic Module
Inspect import chain failures in smoke tests for registry_api.py, rug_pull_monitor.py, signal_analyser.py.
"""

import logging
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'

def ws_write(table, rows):
    import requests
    r = requests.post(WRITE_SERVICE_URL + '/write',
        json={'table': table, 'rows': rows, 'wait': True}, timeout=8)
    return r.status_code == 200

def heartbeat():
    ws_write('service_health', {
        'service': 'diagnose_import_traces_rug_registry_signal',
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    })

def inspect_imports(file_path):
    import ast
    with open(file_path, 'r') as file:
        tree = ast.parse(file.read(), filename=file_path)
    
    resolved_modules = []
    failed_modules = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    __import__(alias.name)
                    resolved_modules.append(alias.name)
                except ImportError:
                    failed_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            try:
                __import__(node.module)
                resolved_modules.append(node.module)
            except ImportError:
                failed_modules.append(node.module)

    return resolved_modules, failed_modules

def run():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger('diagnose_import_traces_rug_registry_signal')
    log.info('Starting...')
    heartbeat()
    
    files_to_inspect = [
        '/home/workspace/zo_sentinel/registry_api.py',
        '/home/workspace/zo_sentinel/rug_pull_monitor.py',
        '/home/workspace/zo_sentinel/signal_analyser.py'
    ]
    
    for file_path in files_to_inspect:
        resolved, failed = inspect_imports(file_path)
        log.info(f'File: {file_path}')
        log.info(f'Resolved Modules: {resolved}')
        log.info(f'Failed Modules: {failed}')
        ws_write('import_diagnosis', {
            'file': file_path,
            'resolved_modules': ','.join(resolved),
            'failed_modules': ','.join(failed),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    while True:
        try:
            heartbeat()
        except Exception as e:
            log.error('Cycle error: %s', e)
        time.sleep(3600)

if __name__ == '__main__':
    run()