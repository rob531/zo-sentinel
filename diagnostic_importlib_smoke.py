import logging
import sys
import importlib
import traceback
import os
import ast

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/importlib_smoke_diagnostic.log')]
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'importlib_smoke_diagnostic'
PROBLEM_MODULES = [
    '/home/workspace/zo_sentinel/registry_api.py',
    '/home/workspace/zo_sentinel/rug_pull_monitor.py',
    '/home/workspace/zo_sentinel/signal_analyser.py',
]
SMOKE_LOG_PATHS = [
    '/home/workspace/logs/smoke_registry_api.log',
    '/home/workspace/logs/smoke_rug_pull_monitor.log',
    '/home/workspace/logs/smoke_signal_analyser.log',
]

def parse_smoke_log(log_path):
    if not os.path.exists(log_path):
        log.warning("Smoke log not found: %s", log_path)
        return None
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        log.info("=== Parsing smoke log: %s ===", log_path)
        log.info("First 3000 chars:\n%s", content[:3000])
        return content
    except Exception as e:
        log.error("Failed to read smoke log %s: %s", log_path, e)
        return None

def check_module_syntax(module_path):
    if not os.path.exists(module_path):
        log.error("Module not found: %s", module_path)
        return False, "File not found"
    try:
        with open(module_path, 'r', encoding='utf-8') as f:
            source = f.read()
        ast.parse(source)
        log.info("Syntax OK: %s", module_path)
        return True, None
    except SyntaxError as e:
        log.error("Syntax error in %s: %s", module_path, e)
        return False, str(e)

def attempt_import_with_trace(module_path):
    module_name = os.path.basename(module_path)
    log.info("Attempting import of: %s", module_name)
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None:
            log.error("Cannot create spec for %s", module_path)
            return False, "spec_from_file_location returned None"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        log.info("Import SUCCESS: %s", module_name)
        return True, None
    except Exception as e:
        tb = traceback.format_exc()
        log.error("Import FAILED for %s: %s", module_name, e)
        log.error("Traceback:\n%s", tb)
        return False, str(e)

def extract_frozen_import_info(traceback_str):
    if '<frozen' in traceback_str:
        log.info("Frozen module detected in traceback")
        lines = traceback_str.split('\n')
        for i, line in enumerate(lines):
            if '<frozen' in line:
                log.info("Frozen line %d: %s", i, line.strip())
    return

def diagnose_module(module_path):
    log.info("=== Diagnosing module: %s ===", module_path)
    results = {
        'path': module_path,
        'syntax_ok': False,
        'import_ok': False,
        'error': None,
    }
    syntax_ok, syntax_err = check_module_syntax(module_path)
    results['syntax_ok'] = syntax_ok
    if not syntax_ok:
        results['error'] = f"Syntax error: {syntax_err}"
        return results
    import_ok, import_err = attempt_import_with_trace(module_path)
    results['import_ok'] = import_ok
    if not import_ok:
        results['error'] = import_err
    return results

def check_importlib_state():
    log.info("=== Checking importlib state ===")
    import importlib
    import importlib.machinery
    log.info("machinery file: %s", importlib.machinery.__file__)
    return True

def scan_for_shadowed_imports(module_path):
    log.info("=== Scanning for shadowed imports in: %s ===", module_path)
    try:
        with open(module_path, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(('import', alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    imports.append(('from', f"{module}.{alias.name}" if module else alias.name))
        for imp_type, imp_name in imports[:20]:
            log.info("  Found %s: %s", imp_type, imp_name)
        return imports
    except Exception as e:
        log.error("Failed to scan imports: %s", e)
        return []

def main():
    log.info("=== Starting importlib smoke diagnostic ===")
    check_importlib_state()
    for smoke_log in SMOKE_LOG_PATHS:
        parse_smoke_log(smoke_log)
    for module_path in PROBLEM_MODULES:
        if os.path.exists(module_path):
            scan_for_shadowed_imports(module_path)
            result = diagnose_module(module_path)
            log.info("Diagnostic result for %s: OK=%s, Error=%s",
                     os.path.basename(module_path), result['import_ok'], result['error'])
        else:
            log.warning("Module not found: %s", module_path)
    log.info("=== Diagnostic complete ===")
    sys.exit(0)

if __name__ == '__main__':
    main()