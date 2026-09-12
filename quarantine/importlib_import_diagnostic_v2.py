import importlib.util
import importlib
import sys
import os
import traceback
import logging
from datetime import datetime, timezone
from pathlib import Path
import json
import ast
import requests

SERVICE_NAME = 'importlib_import_diagnostic_v2'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_PATH = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_PATH)]
)
logger = logging.getLogger(SERVICE_NAME)


def ws_write(table, rows):
    """Write rows via write_service HTTP API."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f'write_service failed for {table}: {e}')
        return None


def ws_query(sql, params=None):
    """Query via write_service HTTP API."""
    payload = {'sql': sql, 'params': params or []}
    try:
        resp = requests.post(f'{WRITE_SERVICE_URL}/query', json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f'write_service query failed: {e}')
        return None


def send_heartbeat(status='running', meta=None):
    """Send service heartbeat."""
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps(meta or {})
    }
    ws_write('service_health', row)


def try_import_module(module_name):
    """Try to import a module and capture the result."""
    result = {
        'module_name': module_name,
        'success': False,
        'error': None,
        'error_type': None,
        'traceback': None
    }
    try:
        mod = importlib.import_module(module_name)
        result['success'] = True
        result['module'] = str(mod)
        logger.info(f'Successfully imported: {module_name}')
        return result
    except ImportError as e:
        result['error'] = str(e)
        result['error_type'] = 'ImportError'
        result['traceback'] = traceback.format_exc()
        logger.warning(f'ImportError for {module_name}: {e}')
        return result
    except Exception as e:
        result['error'] = str(e)
        result['error_type'] = type(e).__name__
        result['traceback'] = traceback.format_exc()
        logger.error(f'Error importing {module_name}: {e}')
        return result


def find_spec_module(module_name):
    """Use importlib.util.find_spec to check module availability."""
    spec = importlib.util.find_spec(module_name)
    if spec:
        logger.info(f'Spec found for {module_name}: origin={spec.origin}, has_location={spec.has_location}')
        return {
            'module_name': module_name,
            'found': True,
            'origin': spec.origin,
            'has_location': spec.has_location,
            'parent': spec.parent if hasattr(spec, 'parent') else None
        }
    else:
        logger.warning(f'No spec found for {module_name}')
        return {
            'module_name': module_name,
            'found': False
        }


def try_module_from_spec(module_name):
    """Use importlib.util.module_from_spec pattern to load a module."""
    result = {
        'module_name': module_name,
        'success': False,
        'spec_found': False,
        'error': None
    }
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            result['spec_found'] = False
            result['error'] = f'No spec for {module_name}'
            logger.warning(result['error'])
            return result
        
        result['spec_found'] = True
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        result['success'] = True
        logger.info(f'Successfully loaded {module_name} via module_from_spec')
        return result
    except Exception as e:
        result['error'] = str(e)
        result['error_type'] = type(e).__name__
        result['traceback'] = traceback.format_exc()
        logger.error(f'module_from_spec failed for {module_name}: {e}')
        return result


def extract_imports_from_file(filepath):
    """Parse Python file and extract all import statements."""
    imports = {'import': [], 'from_import': []}
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports['import'].append({
                        'module': alias.name,
                        'asname': alias.asname,
                        'lineno': node.lineno
                    })
            elif isinstance(node, ast.ImportFrom):
                imports['from_import'].append({
                    'module': node.module,
                    'names': [a.name for a in node.names],
                    'level': node.level,
                    'lineno': node.lineno
                })
    except Exception as e:
        logger.error(f'Failed to parse {filepath}: {e}')
    return imports


def scan_zo_sentinel_files():
    """Scan recently-built files in /home/workspace/zo_sentinel/."""
    sentinel_dir = Path('/home/workspace/zo_sentinel')
    if not sentinel_dir.exists():
        logger.warning(f'Sentinel dir not found: {sentinel_dir}')
        return []
    
    files = list(sentinel_dir.glob('*.py'))
    logger.info(f'Found {len(files)} Python files in {sentinel_dir}')
    
    file_imports = []
    for f in files:
        logger.debug(f'Scanning {f.name}')
        imports = extract_imports_from_file(f)
        file_imports.append({
            'file': f.name,
            'imports': imports
        })
    
    return file_imports


def detect_circular_imports(files_imports):
    """Detect potential circular import patterns."""
    module_deps = {}
    for entry in files_imports:
        file_name = entry['file']
        for imp in entry['imports']['import']:
            mod = imp['module']
            if mod not in module_deps:
                module_deps[mod] = []
            module_deps[mod].append(file_name)
        for imp in entry['imports']['from_import']:
            if imp['module']:
                mod = imp['module']
                if mod not in module_deps:
                    module_deps[mod] = []
                module_deps[mod].append(file_name)
    
    circular_candidates = []
    for module, files in module_deps.items():
        if len(files) > 1:
            circular_candidates.append({
                'module': module,
                'imported_by': files
            })
    
    return circular_candidates


def probe_failed_imports(failed_modules):
    """Probe specific failed imports with detailed diagnostics."""
    results = []
    for module_name in failed_modules:
        logger.info(f'Probing failed import: {module_name}')
        spec_result = find_spec_module(module_name)
        import_result = try_import_module(module_name)
        spec_loader_result = try_module_from_spec(module_name)
        
        results.append({
            'module_name': module_name,
            'spec_result': spec_result,
            'import_result': import_result,
            'spec_loader_result': spec_loader_result
        })
    return results


def get_recent_smoke_failures():
    """Query recent smoke failures from write_service."""
    sql = """
    SELECT service_name, status, last_heartbeat, meta
    FROM service_health
    WHERE status LIKE '%FAIL%' OR status LIKE '%ERROR%'
    ORDER BY last_heartbeat DESC
    LIMIT 20
    """
    result = ws_query(sql)
    return result


def get_recent_build_artifacts():
    """Get recent build artifacts that might have import issues."""
    sql = """
    SELECT artifact_name, file_path, interface
    FROM mesh_memory.build_artifact
    WHERE created_at > NOW() - INTERVAL '24 hours'
    ORDER BY created_at DESC
    LIMIT 50
    """
    result = ws_query(sql)
    return result


def cycle():
    """Run one diagnostic cycle."""
    logger.info('Starting import diagnostic cycle')
    diagnostics = {
        'cycle_ts': datetime.now(timezone.utc).isoformat(),
        'modules_tested': [],
        'circular_imports_detected': [],
        'files_scanned': 0,
        'issues_found': []
    }
    
    # Scan zo_sentinel files
    logger.info('Scanning zo_sentinel files for imports')
    files_imports = scan_zo_sentinel_files()
    diagnostics['files_scanned'] = len(files_imports)
    
    # Detect circular imports
    logger.info('Detecting circular import patterns')
    circular = detect_circular_imports(files_imports)
    diagnostics['circular_imports_detected'] = circular
    
    # Known problematic modules to probe
    problematic_modules = [
        'mcp_server_registry',
        'mcp_registry_facts', 
        'mcp_attestations',
        'mcp_signal_scores',
        'service_health',
        'mesh_memory',
        'threat_intel_ingestor',
        'registry_api',
        'rug_pull_monitor',
        'signal_analyser'
    ]
    
    logger.info(f'Probing {len(problematic_modules)} known modules')
    for mod in problematic_modules:
        probe_result = try_import_module(mod)
        diagnostics['modules_tested'].append(probe_result)
        
        if not probe_result['success']:
            diagnostics['issues_found'].append({
                'module': mod,
                'error': probe_result['error'],
                'error_type': probe_result['error_type']
            })
    
    # Get recent smoke failures
    logger.info('Querying recent smoke failures')
    smoke_failures = get_recent_smoke_failures()
    diagnostics['smoke_failures'] = smoke_failures
    
    # Get recent build artifacts
    logger.info('Querying recent build artifacts')
    artifacts = get_recent_build_artifacts()
    diagnostics['recent_artifacts'] = artifacts
    
    # Write diagnostics to service_health meta
    send_heartbeat(
        status='running',
        meta={
            'diagnostics': diagnostics,
            'circular_count': len(circular),
            'issues_count': len(diagnostics['issues_found']),
            'files_scanned': len(files_imports)
        }
    )
    
    logger.info(f'Cycle complete: {len(diagnostics["issues_found"])} issues found, {len(circular)} circular candidates')
    return diagnostics


def run():
    """Run the diagnostic loop."""
    logger.info(f'{SERVICE_NAME} starting up')
    import time
    
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f'Cycle error: {e}')
        
        time.sleep(60)


if __name__ == '__main__':
    logger.info(f'{SERVICE_NAME} entrypoint')
    run()