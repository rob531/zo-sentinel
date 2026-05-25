import subprocess
import sys
import os
import traceback
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

PROTECTED_FILES = [
    'registry_api.py',
    'rug_pull_monitor.py', 
    'signal_analyser.py'
]

FAILING_MODULES = [
    'registry_api',
    'rug_pull_monitor',
    'signal_analyser'
]

PROJECT_ROOT = '/home/workspace/zo_sentinel'
OUTPUT_FILE = '/tmp/importlib_systemic_diagnosis.json'
LOG_FILE = '/tmp/importlib_diagnosis.log'

def log(msg: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass

def check_single_instance(pid_file: str) -> bool:
    pid = os.getpid()
    try:
        with open(pid_file, 'r') as f:
            existing_pid = int(f.read().strip())
        if existing_pid != pid:
            existing_cmd = None
            try:
                with open(f'/proc/{existing_pid}/cmdline', 'r') as cf:
                    existing_cmd = cf.read()
            except Exception:
                pass
            if existing_cmd:
                log(f"Instance already running with PID {existing_pid}")
                return False
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"Error checking PID file: {e}")
    try:
        with open(pid_file, 'w') as f:
            f.write(str(pid))
    except Exception as e:
        log(f"Error writing PID file: {e}")
    return True

def remove_pid_file(pid_file: str):
    try:
        os.remove(pid_file)
    except Exception:
        pass

def run_subprocess_import(module_name: str, module_path: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """Attempt to import a module in isolation via subprocess. Returns (success, traceback, missing_dep, import_path)."""
    import_script = f"""
import sys
import traceback
sys.path.insert(0, '{module_path}')

try:
    __import__('{module_name}')
    print('IMPORT_SUCCESS')
except ImportError as e:
    print(f'IMPORT_ERROR: {{e}}')
    traceback.print_exc()
except Exception as e:
    print(f'OTHER_ERROR: {{type(e).__name__}}: {{e}}')
    traceback.print_exc()
"""
    try:
        result = subprocess.run(
            [sys.executable, '-c', import_script],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=module_path
        )
        stdout = result.stdout
        stderr = result.stderr
        
        if 'IMPORT_SUCCESS' in stdout:
            return True, "", None, None
        
        missing_dep = None
        import_path = None
        
        for line in stdout.split('\n'):
            if line.startswith('IMPORT_ERROR:'):
                missing_dep = line.replace('IMPORT_ERROR:', '').strip()
            elif 'ModuleNotFoundError' in line or 'ImportError' in line:
                if 'No module named' in line:
                    parts = line.split('No module named')
                    if len(parts) > 1:
                        missing_dep = parts[1].strip().strip("'\"")
        
        for line in stderr.split('\n') + stdout.split('\n'):
            if 'sys.path' in line or 'import' in line.lower():
                if module_path in line:
                    import_path = line.strip()
                    break
        
        full_traceback = stdout + "\n" + stderr
        
        return False, full_traceback, missing_dep, import_path
        
    except subprocess.TimeoutExpired:
        return False, f"Timeout after 30 seconds", None, None
    except Exception as e:
        return False, f"Subprocess error: {str(e)}", None, None

def check_sys_path_integrity(module_path: str) -> Dict:
    """Check sys.path configuration for the module."""
    check_script = f"""
import sys
sys.path.insert(0, '{module_path}')

print("SYSPATH_ENTRIES:")
for i, p in enumerate(sys.path):
    print(f"  [{{i}}] {{p}}")

print("\\nSTD_LIB_CHECK:")
import stdlib_list
stdlib = stdlib_list.stdlib_list()
print(f"  Standard library modules count: {{len(stdlib)}}")
"""
    try:
        result = subprocess.run(
            [sys.executable, '-c', check_script],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=module_path
        )
        return {
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        }
    except Exception as e:
        return {'error': str(e)}

def check_import_chain(module_name: str, module_path: str) -> List[str]:
    """Trace the full import chain for a module."""
    trace_script = f"""
import sys
import importlib
import importlib.util

sys.path.insert(0, '{module_path}')

def trace_import(name, import_stack=None):
    if import_stack is None:
        import_stack = []
    
    if name in import_stack:
        return f"  CIRCULAR: {' -> '.join(import_stack)} -> {{name}}"
    
    new_stack = import_stack + [name]
    
    try:
        spec = importlib.util.find_spec(name)
        if spec is None:
            return f"  NOT_FOUND: {{name}}"
        
        if spec.submodule_search_locations:
            for location in spec.submodule_search_locations[:3]:
                return f"  FOUND: {{name}} in {{location}}"
        elif spec.parent:
            return f"  FOUND: {{name}} (parent: {{spec.parent}})"
        else:
            return f"  FOUND: {{name}}"
            
    except Exception as e:
        return f"  ERROR ({{name}}): {{type(e).__name__}}: {{e}}"

print("IMPORT_CHAIN_TRACE:")
print(f"  Target: {{module_name}}")
for dep in ['os', 'json', 'requests', 'fastapi', 'duckdb']:
    print(trace_import(dep))
"""
    try:
        result = subprocess.run(
            [sys.executable, '-c', trace_script],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=module_path
        )
        lines = []
        for line in result.stdout.split('\n'):
            if 'NOT_FOUND' in line or 'ERROR' in line or 'CIRCULAR' in line or 'FOUND' in line:
                lines.append(line.strip())
        return lines
    except Exception as e:
        return [f"Error tracing: {str(e)}"]

def diagnose_module(module_name: str, module_path: str) -> Dict:
    """Full diagnostic for a single module."""
    log(f"Diagnosing module: {module_name}")
    
    diagnosis = {
        'module_name': module_name,
        'module_path': module_path,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'import_result': None,
        'missing_dependency': None,
        'import_path': None,
        'syspath_check': None,
        'import_chain': None,
        'findings': []
    }
    
    success, tb, missing_dep, import_path = run_subprocess_import(module_name, module_path)
    
    diagnosis['import_result'] = {
        'success': success,
        'traceback': tb[:2000] if len(tb) > 2000 else tb
    }
    diagnosis['missing_dependency'] = missing_dep
    diagnosis['import_path'] = import_path
    
    syspath = check_sys_path_integrity(module_path)
    diagnosis['syspath_check'] = syspath
    
    if not success:
        chain = check_import_chain(module_name, module_path)
        diagnosis['import_chain'] = chain
        
        if missing_dep:
            diagnosis['findings'].append(f"MISSING_DEPENDENCY: {missing_dep}")
        if 'No module named' in tb:
            diagnosis['findings'].append("IMPORT_PATH_BROKEN: Check sys.path configuration")
        if 'circular' in tb.lower():
            diagnosis['findings'].append("CIRCULAR_IMPORT: Potential circular dependency")
        if 'syntax' in tb.lower():
            diagnosis['findings'].append("SYNTAX_ERROR: Module has syntax errors")
        if import_path:
            diagnosis['findings'].append(f"IMPORT_PATH: {import_path}")
    
    return diagnosis

def write_diagnostic_record(results: List[Dict]):
    """Write diagnostic record to temp file."""
    record = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'diagnostics': results,
        'summary': {
            'total_modules': len(results),
            'failed_count': sum(1 for r in results if not r['import_result']['success']),
            'missing_deps': list(set(r['missing_dependency'] for r in results if r['missing_dependency']))
        }
    }
    
    try:
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(record, f, indent=2)
        log(f"Diagnostic record written to: {OUTPUT_FILE}")
    except Exception as e:
        log(f"Error writing diagnostic record: {e}")

def print_findings(results: List[Dict]):
    """Print findings to stdout for human review."""
    print("\n" + "=" * 80)
    print("IMPORTLIB SYSTEMIC FAILURES DIAGNOSTIC REPORT")
    print("=" * 80)
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"Module Path: {PROJECT_ROOT}")
    print()
    
    for diag in results:
        print(f"\n{'─' * 40}")
        print(f"Module: {diag['module_name']}")
        print(f"{'─' * 40}")
        
        if diag['import_result']['success']:
            print("  Status: ✓ IMPORT SUCCESSFUL")
        else:
            print("  Status: ✗ IMPORT FAILED")
            
            if diag['missing_dependency']:
                print(f"  Missing Dependency: {diag['missing_dependency']}")
            
            if diag['findings']:
                print("  Findings:")
                for finding in diag['findings']:
                    print(f"    • {finding}")
            
            if diag['import_chain']:
                print("  Import Chain Issues:")
                for issue in diag['import_chain']:
                    print(f"    {issue}")
            
            if diag['syspath_check'] and diag['syspath_check'].get('stdout'):
                print("  sys.path Analysis:")
                for line in diag['syspath_check']['stdout'].split('\n')[:10]:
                    if line.strip():
                        print(f"    {line}")
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    failed = [r for r in results if not r['import_result']['success']]
    missing_deps = list(set(r['missing_dependency'] for r in failed if r['missing_dependency']))
    
    print(f"Total Modules Tested: {len(results)}")
    print(f"Failed: {len(failed)}")
    print(f"Succeeded: {len(results) - len(failed)}")
    
    if missing_deps:
        print(f"\nMissing Dependencies Identified:")
        for dep in missing_deps:
            print(f"  • {dep}")
    else:
        print(f"\nNo specific missing dependencies identified.")
        print(f"Possible causes: circular imports, sys.path misconfiguration, syntax errors")
    
    print(f"\nFull diagnostic record: {OUTPUT_FILE}")
    print("=" * 80 + "\n")

def run():
    """Main diagnostic loop."""
    log("Starting importlib systemic failures diagnostic")
    
    pid_file = '/tmp/diagnose_importlib_systemic_failures.pid'
    if not check_single_instance(pid_file):
        log("Another instance is already running")
        return
    
    try:
        results = []
        
        for module_name in FAILING_MODULES:
            module_path = PROJECT_ROOT
            diagnosis = diagnose_module(module_name, module_path)
            results.append(diagnosis)
            
            if not diagnosis['import_result']['success']:
                log(f"FAILED: {module_name} - {diagnosis.get('missing_dependency', 'Unknown error')}")
            else:
                log(f"SUCCESS: {module_name}")
        
        print_findings(results)
        write_diagnostic_record(results)
        
        log("Diagnostic complete")
        
    finally:
        remove_pid_file(pid_file)

if __name__ == '__main__':
    run()