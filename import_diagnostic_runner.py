import logging
import sys
import os
import traceback
import importlib
import importlib.abc
import importlib.machinery
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set, Dict, Any

SERVICE_NAME = 'import_diagnostic_runner'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

SENTINEL_PATH = '/home/workspace/zo_sentinel'
TARGET_MODULES = [
    'registry_api',
    'rug_pull_monitor', 
    'signal_analyser'
]

class ImportTracer(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self):
        self.attempts: list[Dict[str, Any]] = []
        self.failed: list[Dict[str, Any]] = []
        
    def find_spec(self, fullname, path, target=None):
        self.attempts.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'fullname': fullname,
            'path': path,
            'target': str(target) if target else None
        })
        return None
    
    def create_module(self, spec):
        return None
    
    def exec_module(self, module):
        return None

def trace_imports(module_name: str) -> Dict[str, Any]:
    result = {
        'module': module_name,
        'attempts': [],
        'success': False,
        'error': None,
        'error_trace': None,
        'resolved_path': None,
        'sys_modules_state_before': {},
        'sys_modules_state_after': {}
    }
    
    tracer = ImportTracer()
    sys.meta_path.insert(0, tracer)
    
    module_prefix = module_name.split('.')[0]
    result['sys_modules_state_before'] = {
        k: str(v) for k, v in sys.modules.items() 
        if k.startswith(module_prefix) or k.startswith('zo_')
    }
    
    try:
        result['resolved_path'] = importlib.util.find_spec(module_name)
        if result['resolved_path']:
            importlib.import_module(module_name)
            result['success'] = True
    except Exception as e:
        result['error'] = str(e)
        result['error_trace'] = traceback.format_exc()
        result['failed'] = tracer.failed.copy()
    finally:
        sys.meta_path.remove(tracer)
    
    result['sys_modules_state_after'] = {
        k: str(v) for k, v in sys.modules.items()
        if k.startswith(module_prefix) or k.startswith('zo_')
    }
    
    result['attempts'] = tracer.attempts.copy()
    
    return result

def check_file_existence(module_name: str) -> Dict[str, Any]:
    check = {
        'module': module_name,
        'candidate_paths': [],
        'found': False,
        'found_path': None
    }
    
    base_path = Path(SENTINEL_PATH)
    
    for suffix in ['', '/__init__.py']:
        candidate = base_path / f'{module_name}{suffix}'
        check['candidate_paths'].append(str(candidate))
        if candidate.exists():
            check['found'] = True
            check['found_path'] = str(candidate)
            break
    
    pyi_path = base_path / f'{module_name}.pyi'
    if pyi_path.exists():
        check['candidate_paths'].append(str(pyi_path))
        if not check['found']:
            check['found'] = True
            check['found_path'] = str(pyi_path)
    
    return check

def check_import_dependencies(module_name: str) -> Dict[str, Any]:
    deps = {
        'module': module_name,
        'imports': [],
        'missing': [],
        'circular_suspect': []
    }
    
    pyi_path = Path(SENTINEL_PATH) / f'{module_name}.py'
    if not pyi_path.exists():
        return deps
    
    try:
        content = pyi_path.read_text(encoding='utf-8')
        import re
        import_stmts = re.findall(r'^\s*(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)', content, re.MULTILINE)
        
        for imp in import_stmts:
            base = imp.split('.')[0]
            deps['imports'].append(imp)
            
            try:
                spec = importlib.util.find_spec(base)
                if spec is None:
                    deps['missing'].append(imp)
            except Exception:
                deps['missing'].append(imp)
        
        for imp in deps['imports']:
            if imp in sys.modules:
                mod = sys.modules[imp]
                if hasattr(mod, '__file__') and mod.__file__ and SENTINEL_PATH in str(mod.__file__):
                    deps['circular_suspect'].append(imp)
                    
    except Exception as e:
        deps['error'] = str(e)
    
    return deps

def write_diagnostic_report(results: list[Dict[str, Any]], report_path: str):
    lines = []
    lines.append('=' * 80)
    lines.append(f'IMPORT DIAGNOSTIC REPORT')
    lines.append(f'Generated: {datetime.now(timezone.utc).isoformat()}')
    lines.append(f'Target modules: {", ".join(TARGET_MODULES)}')
    lines.append('=' * 80)
    
    for r in results:
        lines.append(f"\n{'─' * 80}")
        lines.append(f"MODULE: {r['module']}")
        lines.append(f"{'─' * 80}")
        
        lines.append(f"\n[File Existence]")
        fe = r.get('file_existence', {})
        lines.append(f"  Found: {fe.get('found', False)}")
        if fe.get('found_path'):
            lines.append(f"  Path: {fe['found_path']}")
        else:
            lines.append(f"  Candidates checked: {fe.get('candidate_paths', [])}")
        
        lines.append(f"\n[Import Trace]")
        lines.append(f"  Success: {r.get('import_result', {}).get('success', False)}")
        lines.append(f"  Resolved spec: {r.get('import_result', {}).get('resolved_path')}")
        
        err = r.get('import_result', {}).get('error')
        if err:
            lines.append(f"  ERROR: {err}")
            lines.append(f"  Traceback:\n{r.get('import_result', {}).get('error_trace', '')}")
        
        lines.append(f"\n[Dependencies]")
        dep = r.get('dependencies', {})
        lines.append(f"  Imports found: {dep.get('imports', [])}")
        lines.append(f"  Missing: {dep.get('missing', [])}")
        lines.append(f"  Circular suspects: {dep.get('circular_suspect', [])}")
        
        lines.append(f"\n[Import Attempts ({len(r.get('import_result', {}).get('attempts', []))})]")
        for attempt in r.get('import_result', {}).get('attempts', [])[-10:]:
            lines.append(f"  {attempt.get('timestamp')}: {attempt.get('fullname')}")
        
    lines.append(f"\n{'=' * 80}")
    lines.append("END OF REPORT")
    lines.append('=' * 80)
    
    report_content = '\n'.join(lines)
    Path(report_path).write_text(report_content, encoding='utf-8')
    return report_content

def main():
    logger.info(f'Starting import chain diagnostic for: {TARGET_MODULES}')
    
    all_results = []
    
    for module in TARGET_MODULES:
        logger.info(f'Diagnosing: {module}')
        result = {
            'module': module,
            'file_existence': check_file_existence(module),
            'import_result': trace_imports(module),
            'dependencies': check_imports_dependencies(module)
        }
        all_results.append(result)
        
        logger.info(f"  File exists: {result['file_existence']['found']}")
        logger.info(f"  Import success: {result['import_result']['success']}")
        if result['import_result']['error']:
            logger.error(f"  Import error: {result['import_result']['error']}")
        logger.info(f"  Missing deps: {result['dependencies'].get('missing', [])}")
    
    report_path = f'/home/workspace/logs/import_diagnostic_report_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")}.txt'
    report = write_diagnostic_report(all_results, report_path)
    
    logger.info(f'Diagnostic report written to: {report_path}')
    print(report)
    
    sys.exit(0)

if __name__ == '__main__':
    main()