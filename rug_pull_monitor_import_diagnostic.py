#!/usr/bin/env python3
"""
rug_pull_monitor_import_diagnostic.py -- Diagnostic for rug_pull_monitor.py smoke failure.

Parses rug_pull_monitor.py, identifies missing imports or dependency conflicts,
logs findings to service_health meta column, and reports findings.

SPEC REF: PRODUCT_SPEC §6 dependency declaration rule.

This file does NOT modify rug_pull_monitor.py (protected).
"""
import importlib
import importlib.util
import ast
import sys
import os
import traceback
from datetime import datetime, timezone
from typing import Optional

# deps: requests
import requests

SERVICE_NAME = 'rug_pull_monitor_import_diagnostic'
TARGET_MODULE = 'rug_pull_monitor'
TARGET_PATH = os.path.join(os.path.dirname(__file__), f'{TARGET_MODULE}.py')
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_URL = 'http://127.0.0.1:8772/query'


def ws_write(table: str, rows: dict | list, wait: bool = True) -> dict:
    """Write rows to DuckDB table via write_service."""
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str, params: list = None) -> dict:
    """Execute SELECT against DuckDB via write_service /query."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if 'rows' in body and 'data' not in body:
        body['data'] = [[r[k] for k in r.keys()] for r in body['rows']]
    return body


def log_to_service_health(finding: str, status: str = 'warning') -> None:
    """Log diagnostic findings to service_health meta column."""
    try:
        # Fetch current meta JSON if present
        current_meta = {}
        try:
            result = ws_query(
                "SELECT meta FROM service_health WHERE service = ? ORDER BY timestamp DESC LIMIT 1",
                [SERVICE_NAME]
            )
            rows = result.get('data', [])
            if rows and rows[0] and rows[0][0]:
                import json
                current_meta = json.loads(rows[0][0])
        except Exception:
            pass

        import json
        # Append diagnostic finding to meta
        if 'import_diagnostic_findings' not in current_meta:
            current_meta['import_diagnostic_findings'] = []
        current_meta['import_diagnostic_findings'].append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'status': status,
            'finding': finding,
        })
        current_meta['last_check'] = datetime.now(timezone.utc).isoformat()

        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': status,
            'meta': json.dumps(current_meta),
        })
    except Exception as e:
        print(f"[WARN] Failed to log to service_health: {e}")


def extract_imports_from_ast(filepath: str) -> list[dict]:
    """Parse Python file and extract import statements."""
    findings = []
    try:
        with open(filepath, 'r', encoding='utf-8') as fh:
            source = fh.read()
        tree = ast.parse(source, filename=filepath)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    findings.append({
                        'type': 'import',
                        'module': alias.name,
                        'asname': alias.asname,
                        'line': node.lineno,
                    })
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    findings.append({
                        'type': 'import_from',
                        'module': node.module,
                        'name': alias.name,
                        'asname': alias.asname,
                        'line': node.lineno,
                    })
    except SyntaxError as e:
        findings.append({
            'type': 'syntax_error',
            'error': str(e),
            'line': getattr(e, 'lineno', None),
        })
    except Exception as e:
        findings.append({
            'type': 'parse_error',
            'error': str(e),
        })
    return findings


def check_import_resolve(module_name: str) -> dict:
    """Attempt to resolve a module import and return status."""
    result = {
        'module': module_name,
        'resolves': False,
        'version': None,
        'error': None,
    }
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            result['resolves'] = True
            try:
                mod = importlib.import_module(module_name)
                result['version'] = getattr(mod, '__version__', None)
                # Also try version from pkg_resources for packages
                try:
                    import pkg_resources
                    dist = pkg_resources.get_distribution(module_name.split('.')[0])
                    result['version'] = dist.version
                except Exception:
                    pass
            except ImportError as e:
                result['error'] = str(e)
        else:
            result['error'] = 'module not found in sys.path'
    except Exception as e:
        result['error'] = str(e)
    return result


def check_rug_pull_monitor_imports() -> dict:
    """Full import diagnostic for rug_pull_monitor.py."""
    findings = {
        'target_file': TARGET_PATH,
        'exists': os.path.exists(TARGET_PATH),
        'imports': [],
        'missing_imports': [],
        'dependency_conflicts': [],
        'summary': 'OK',
        'errors': [],
    }

    if not os.path.exists(TARGET_PATH):
        findings['errors'].append(f"Target file not found: {TARGET_PATH}")
        findings['summary'] = 'FILE_NOT_FOUND'
        return findings

    # Step 1: Extract all imports from AST
    import_entries = extract_imports_from_ast(TARGET_PATH)
    findings['imports'] = import_entries

    # Step 2: Resolve each import
    stdlib_modules = {
        'os', 'sys', 'json', 'time', 'hashlib', 'signal', 'ast',
        'importlib', 'importlib.util', 'importlib.machinery',
        'traceback', 'datetime', 'types', 'typing', 'urllib.parse',
        'collections', 'functools', 're', 'pathlib', 'contextlib',
    }

    for entry in import_entries:
        if entry['type'] == 'syntax_error':
            findings['errors'].append(f"Syntax error at line {entry.get('line')}: {entry.get('error')}")
            findings['summary'] = 'SYNTAX_ERROR'
            continue
        if entry['type'] == 'parse_error':
            findings['errors'].append(f"Parse error: {entry.get('error')}")
            findings['summary'] = 'PARSE_ERROR'
            continue

        if entry['type'] == 'import':
            module = entry['module']
        elif entry['type'] == 'import_from':
            module = entry['module']
        else:
            module = entry.get('module', 'unknown')

        if not module:
            continue

        # Skip modules we already know are stdlib
        base = module.split('.')[0]
        if base in stdlib_modules:
            continue

        # Try to resolve
        check = check_import_resolve(module)
        if not check['resolves']:
            findings['missing_imports'].append({
                'module': module,
                'error': check['error'],
                'entry': entry,
            })
            findings['summary'] = 'MISSING_IMPORTS'
        elif check.get('error') and 'conflict' in check['error'].lower():
            findings['dependency_conflicts'].append({
                'module': module,
                'error': check['error'],
                'entry': entry,
            })
            findings['summary'] = 'CONFLICT'

    # Step 3: Try importing the module as a whole (smoke test)
    if findings['summary'] in ('OK', 'MISSING_IMPORTS'):
        try:
            # Remove cached import if any
            if TARGET_MODULE in sys.modules:
                del sys.modules[TARGET_MODULE]
            spec = importlib.util.spec_from_file_location(TARGET_MODULE, TARGET_PATH)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[TARGET_MODULE] = module
                spec.loader.exec_module(module)
                findings['module_loads'] = True
                findings['summary'] = 'OK'
        except Exception as e:
            findings['module_loads'] = False
            findings['errors'].append(f"Module import error: {type(e).__name__}: {e}")
            findings['traceback'] = traceback.format_exc()
            if findings['summary'] == 'OK':
                findings['summary'] = 'IMPORT_ERROR'

    return findings


def report_findings(findings: dict) -> None:
    """Print human-readable diagnostic report."""
    print(f"\n{'='*60}")
    print(f"  rug_pull_monitor_import_diagnostic")
    print(f"{'='*60}")
    print(f"  Target file: {findings['target_file']}")
    print(f"  File exists : {findings['exists']}")
    print(f"  Summary     : {findings['summary']}")
    print()

    if findings['errors']:
        print(f"  ERRORS ({len(findings['errors'])}):")
        for err in findings['errors']:
            print(f"    [!] {err}")
        print()

    if findings['missing_imports']:
        print(f"  MISSING IMPORTS ({len(findings['missing_imports'])}):")
        for miss in findings['missing_imports']:
            print(f"    [-] {miss['module']}  (line {miss['entry'].get('line', '?')})")
            print(f"        Error: {miss['error']}")
        print()

    if findings['dependency_conflicts']:
        print(f"  DEPENDENCY CONFLICTS ({len(findings['dependency_conflicts'])}):")
        for conflict in findings['dependency_conflicts']:
            print(f"    [!] {conflict['module']}: {conflict['error']}")
        print()

    if findings['imports']:
        print(f"  IMPORTED MODULES ({len(findings['imports'])}):")
        seen = set()
        for imp in findings['imports']:
            key = imp.get('module', imp.get('name', '?'))
            if key not in seen:
                print(f"    [+] {key}")
                seen.add(key)
        print()

    print(f"  Module smoke load : {findings.get('module_loads', 'N/A')}")
    print(f"{'='*60}\n")


def log_summary_to_service_health(findings: dict) -> None:
    """Log a summary of findings to service_health meta."""
    import json

    meta = {
        'diagnostic': 'rug_pull_monitor_import_diagnostic',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'target': findings['target_file'],
        'summary': findings['summary'],
        'file_exists': findings['exists'],
        'errors': findings['errors'],
        'missing_imports': [m['module'] for m in findings['missing_imports']],
        'conflicts': [c['module'] for c in findings['dependency_conflicts']],
        'import_count': len(findings['imports']),
        'module_loads': findings.get('module_loads'),
    }

    status = 'healthy'
    if findings['summary'] != 'OK':
        status = 'warning'

    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': status,
            'meta': json.dumps(meta),
        })
        print(f"[+] Logged diagnostic summary to service_health (status={status})")
    except Exception as e:
        print(f"[WARN] Failed to write to service_health: {e}")


def run() -> dict:
    """Run the full diagnostic and return findings dict."""
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting rug_pull_monitor import diagnostic...")

    findings = check_rug_pull_monitor_imports()
    report_findings(findings)
    log_summary_to_service_health(findings)

    print(f"[{datetime.now(timezone.utc).isoformat()}] Diagnostic complete: {findings['summary']}")
    return findings


if __name__ == '__main__':
    findings = run()
    sys.exit(0 if findings['summary'] == 'OK' else 1)
