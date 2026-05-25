#!/usr/bin/env python3
import sys
import os
import traceback
import importlib
import importlib.util
from datetime import datetime
from typing import Dict, List, Any, Optional

SERVICE_NAME = 'signal_analyser_import_fix'
LOG_FILE = '/home/workspace/logs/signal_analyser_import_fix.log'

sys.path.insert(0, '/home/workspace')

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'


def log(msg: str) -> None:
    ts = datetime.utcnow().isoformat() + 'Z'
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    import requests
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=10
        )
        return resp.json()
    except Exception as e:
        log(f"ws_write error: {e}")
        return None


def send_heartbeat(status: str = 'running', meta: str = '') -> None:
    ts = datetime.utcnow().isoformat() + 'Z'
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': status,
        'meta': meta
    }])


def extract_imports_from_source(source: str) -> List[Dict[str, str]]:
    imports = []
    for line in source.split('\n'):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            import_type = 'import' if stripped.startswith('import ') else 'from'
            if ' as ' in stripped:
                name = stripped.split(' as ')[0].replace('import ', '').replace('from ', '').strip()
            else:
                name = stripped.replace('import ', '').replace('from ', '').split('(')[0].strip().split(' as ')[0]
            name = name.split('.')[0]
            imports.append({
                'type': import_type,
                'name': name,
                'line': stripped
            })
    return imports


def check_module_importable(module_name: str) -> Dict[str, Any]:
    result = {
        'module': module_name,
        'importable': False,
        'error': None,
        'error_type': None,
        'suggested_fix': None
    }
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            result['error'] = f"Module '{module_name}' not found in sys.path"
            result['error_type'] = 'ModuleNotFoundError'
            result['suggested_fix'] = f"Module '{module_name}' not found. Check if package is installed or path is correct."
            return result
        if spec.loader is None:
            result['error'] = f"Module '{module_name}' found but has no loader (namespace package?)"
            result['error_type'] = 'ImportError'
            return result
        importlib.import_module(module_name)
        result['importable'] = True
        return result
    except ModuleNotFoundError as e:
        result['error'] = str(e)
        result['error_type'] = 'ModuleNotFoundError'
        result['suggested_fix'] = f"Install missing module: pip install {module_name}"
        return result
    except SyntaxError as e:
        result['error'] = f"Syntax error in {module_name}: {e}"
        result['error_type'] = 'SyntaxError'
        result['suggested_fix'] = f"Fix syntax error in {module_name}: {e}"
        return result
    except ImportError as e:
        result['error'] = str(e)
        result['error_type'] = 'ImportError'
        result['suggested_fix'] = f"Check import chain: {e}"
        return result
    except Exception as e:
        result['error'] = f"{type(e).__name__}: {e}"
        result['error_type'] = type(e).__name__
        result['suggested_fix'] = f"Investigate {type(e).__name__} in {module_name}"
        return result


def check_file_exists(file_path: str) -> bool:
    return os.path.isfile(file_path)


def read_source_file(file_path: str) -> Optional[str]:
    try:
        with open(file_path, 'r') as f:
            return f.read()
    except Exception as e:
        log(f"Error reading {file_path}: {e}")
        return None


def diagnose_signal_analyser(source: str) -> Dict[str, Any]:
    diagnosis = {
        'file': '/home/workspace/zo_sentinel/signal_analyser.py',
        'exists': check_file_exists('/home/workspace/zo_sentinel/signal_analyser.py'),
        'line_10_content': None,
        'imports_found': [],
        'import_results': [],
        'problems': [],
        'fixes': []
    }
    
    lines = source.split('\n')
    diagnosis['line_10_content'] = lines[9] if len(lines) > 9 else 'N/A'
    
    imports = extract_imports_from_source(source)
    diagnosis['imports_found'] = imports
    
    for imp in imports:
        result = check_module_importable(imp['name'])
        result['source_line'] = imp['line']
        result['import_type'] = imp['type']
        diagnosis['import_results'].append(result)
        
        if not result['importable']:
            diagnosis['problems'].append({
                'module': imp['name'],
                'error': result['error'],
                'error_type': result['error_type'],
                'line': imp['line']
            })
            if result['suggested_fix']:
                diagnosis['fixes'].append(result['suggested_fix'])
    
    return diagnosis


def generate_diagnostic_report(diagnosis: Dict[str, Any]) -> str:
    report = []
    report.append("=" * 70)
    report.append("SIGNAL_ANALYSER IMPORT DIAGNOSTIC REPORT")
    report.append(f"Generated: {datetime.utcnow().isoformat()}Z")
    report.append("=" * 70)
    report.append("")
    
    report.append(f"File: {diagnosis['file']}")
    report.append(f"Exists: {diagnosis['exists']}")
    report.append(f"Line 10 content: {diagnosis['line_10_content']}")
    report.append("")
    
    report.append("-" * 70)
    report.append("IMPORT ANALYSIS")
    report.append("-" * 70)
    
    for result in diagnosis['import_results']:
        status = "OK" if result['importable'] else "FAIL"
        report.append(f"  [{status}] {result['module']}")
        if not result['importable']:
            report.append(f"       Line: {result.get('source_line', 'N/A')}")
            report.append(f"       Error: {result['error']}")
            report.append(f"       Type:  {result['error_type']}")
            if result['suggested_fix']:
                report.append(f"       Fix:   {result['suggested_fix']}")
    
    report.append("")
    report.append("-" * 70)
    report.append("SUMMARY")
    report.append("-" * 70)
    
    total = len(diagnosis['import_results'])
    success = sum(1 for r in diagnosis['import_results'] if r['importable'])
    failed = total - success
    
    report.append(f"Total imports: {total}")
    report.append(f"Successful:    {success}")
    report.append(f"Failed:        {failed}")
    report.append("")
    
    if diagnosis['problems']:
        report.append("PROBLEMS DETECTED:")
        for i, prob in enumerate(diagnosis['problems'], 1):
            report.append(f"  {i}. Module: {prob['module']}")
            report.append(f"     Line: {prob['line']}")
            report.append(f"     Error: {prob['error']}")
            report.append("")
    
    if diagnosis['fixes']:
        report.append("SUGGESTED FIXES:")
        for i, fix in enumerate(diagnosis['fixes'], 1):
            report.append(f"  {i}. {fix}")
        report.append("")
    
    report.append("=" * 70)
    
    return '\n'.join(report)


def main():
    log("Starting signal_analyser import diagnostic")
    send_heartbeat(status='running', meta='Starting import diagnostic')
    
    SIGNAL_ANALYSER_PATH = '/home/workspace/zo_sentinel/signal_analyser.py'
    
    if not check_file_exists(SIGNAL_ANALYSER_PATH):
        log(f"ERROR: signal_analyser.py not found at {SIGNAL_ANALYSER_PATH}")
        send_heartbeat(status='error', meta='File not found')
        sys.exit(1)
    
    log(f"Reading signal_analyser.py from {SIGNAL_ANALYSER_PATH}")
    source = read_source_file(SIGNAL_ANALYSER_PATH)
    
    if source is None:
        log("ERROR: Could not read signal_analyser.py source")
        send_heartbeat(status='error', meta='Could not read source')
        sys.exit(1)
    
    log("Running import diagnostics...")
    diagnosis = diagnose_signal_analyser(source)
    
    report = generate_diagnostic_report(diagnosis)
    print(report)
    
    log_file = '/home/workspace/logs/signal_analyser_import_report.log'
    try:
        with open(log_file, 'w') as f:
            f.write(report)
        log(f"Report written to {log_file}")
    except Exception as e:
        log(f"Error writing report: {e}")
    
    if diagnosis['problems']:
        log(f"FAILURE: {len(diagnosis['problems'])} import problem(s) detected")
        send_heartbeat(status='error', meta=f"{len(diagnosis['problems'])} imports failed")
        sys.exit(1)
    else:
        log("SUCCESS: All imports are valid")
        send_heartbeat(status='ok', meta='All imports passed')
        sys.exit(0)


if __name__ == '__main__':
    main()