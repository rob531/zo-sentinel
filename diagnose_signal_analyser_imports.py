import ast
import sys
import os
import traceback
import logging
from pathlib import Path
from datetime import datetime, timezone

LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / 'diagnose_signal_analyser_imports.log'

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('diagnose_signal_analyser_imports')

SERVICE_NAME = 'diagnose_signal_analyser_imports'
SCRIPT_DIR = Path('/home/workspace/zo_sentinel')
TARGET_MODULE = 'signal_analyser'
TARGET_PATH = SCRIPT_DIR / f'{TARGET_MODULE}.py'


def get_imports_from_source(source_path: Path) -> list:
    imports = []
    try:
        with open(source_path, 'r') as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(('import', alias.name, alias.asname or alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    imports.append(('from', module, alias.name))
    except Exception as e:
        logger.error(f"Failed to parse {source_path}: {e}")
    return imports


def check_module_on_disk(module_name: str) -> dict:
    result = {
        'module_name': module_name,
        'found': False,
        'path': None,
        'error': None,
        'has_syntax_error': False
    }
    
    try:
        spec = __import__('importlib.util').util.find_spec(module_name)
        if spec and spec.origin:
            result['found'] = True
            result['path'] = spec.origin
            result['type'] = 'module' if spec.submodule_search_locations else 'package'
            
            if spec.origin.endswith('.py'):
                try:
                    with open(spec.origin, 'r') as f:
                        ast.parse(f.read())
                    logger.debug(f"  Syntax OK: {spec.origin}")
                except SyntaxError as se:
                    result['has_syntax_error'] = True
                    result['syntax_error'] = str(se)
                    logger.error(f"  SYNTAX ERROR in {spec.origin}: {se}")
        else:
            if spec is None:
                result['error'] = 'Module not found by importlib'
                logger.warning(f"  NOT FOUND: {module_name}")
            elif spec.submodule_search_locations is None and not spec.origin:
                result['error'] = 'Package namespace without __init__.py'
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"  Error checking {module_name}: {e}")
    
    return result


def diagnose_module(target_path: Path) -> dict:
    report = {
        'target_path': str(target_path),
        'exists': target_path.exists(),
        'imports': [],
        'module_check_results': [],
        'critical_errors': [],
        'summary': {}
    }
    
    if not report['exists']:
        report['critical_errors'].append(f"Target module does not exist: {target_path}")
        return report
    
    report['imports'] = get_imports_from_source(target_path)
    logger.info(f"Found {len(report['imports'])} import statements in {target_path.name}")
    
    for imp_type, module, name in report['imports']:
        logger.debug(f"  {imp_type}: {module} -> {name}")
    
    for imp_type, module, name in report['imports']:
        if imp_type == 'from' and module:
            check_result = check_module_on_disk(module)
            report['module_check_results'].append(check_result)
        elif imp_type == 'import':
            check_result = check_module_on_disk(module)
            report['module_check_results'].append(check_result)
    
    for mr in report['module_check_results']:
        if mr.get('has_syntax_error'):
            report['critical_errors'].append(f"SYNTAX ERROR in {mr['path']}: {mr.get('syntax_error', 'unknown')}")
        if not mr['found']:
            report['critical_errors'].append(f"MODULE NOT FOUND: {mr['module_name']}")
    
    report['summary'] = {
        'total_imports': len(report['imports']),
        'missing_modules': sum(1 for m in report['module_check_results'] if not m['found']),
        'syntax_errors': sum(1 for m in report['module_check_results'] if m.get('has_syntax_error')),
        'total_errors': len(report['critical_errors'])
    }
    
    return report


def attempt_live_import(target_module: str) -> dict:
    result = {
        'attempted': True,
        'success': False,
        'error_type': None,
        'error_message': None,
        'traceback_lines': []
    }
    
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        exec(f"import {target_module}", {'__name__': target_module})
        result['success'] = True
        logger.info(f"Live import of {target_module} succeeded")
    except SyntaxError as se:
        result['error_type'] = 'SyntaxError'
        result['error_message'] = str(se)
        tb = traceback.format_exc()
        result['traceback_lines'] = tb.split('\n')
        logger.error(f"Live import SyntaxError: {se}")
    except ImportError as ie:
        result['error_type'] = 'ImportError'
        result['error_message'] = str(ie)
        tb = traceback.format_exc()
        result['traceback_lines'] = tb.split('\n')
        logger.error(f"Live import ImportError: {ie}")
    except NameError as ne:
        result['error_type'] = 'NameError'
        result['error_message'] = str(ne)
        tb = traceback.format_exc()
        result['traceback_lines'] = tb.split('\n')
        logger.error(f"Live import NameError: {ne}")
    except Exception as e:
        result['error_type'] = type(e).__name__
        result['error_message'] = str(e)
        tb = traceback.format_exc()
        result['traceback_lines'] = tb.split('\n')
        logger.error(f"Live import {type(e).__name__}: {e}")
    
    return result


def main():
    logger.info("=" * 60)
    logger.info(f"DIAGNOSTIC RUN: {datetime.now(timezone.utc).isoformat()}")
    logger.info(f"Target: {TARGET_PATH}")
    logger.info("=" * 60)
    
    report = diagnose_module(TARGET_PATH)
    
    logger.info("-" * 40)
    logger.info("DIAGNOSTIC RESULTS:")
    logger.info("-" * 40)
    logger.info(f"Target exists: {report['exists']}")
    logger.info(f"Total imports found: {report['summary']['total_imports']}")
    logger.info(f"Missing modules: {report['summary']['missing_modules']}")
    logger.info(f"Syntax errors in deps: {report['summary']['syntax_errors']}")
    
    if report['critical_errors']:
        logger.info("CRITICAL ERRORS:")
        for i, err in enumerate(report['critical_errors'], 1):
            logger.info(f"  {i}. {err}")
    
    if report['module_check_results']:
        logger.info("MODULE CHECK RESULTS:")
        for mr in report['module_check_results']:
            status = "OK" if mr['found'] and not mr.get('has_syntax_error') else "FAIL"
            logger.info(f"  [{status}] {mr['module_name']}: found={mr['found']}, path={mr.get('path', 'N/A')}")
            if mr.get('has_syntax_error'):
                logger.info(f"         SYNTAX ERROR: {mr.get('syntax_error', 'unknown')}")
            if mr.get('error'):
                logger.info(f"         ERROR: {mr['error']}")
    
    live_result = attempt_live_import(TARGET_MODULE)
    logger.info("-" * 40)
    logger.info(f"LIVE IMPORT ATTEMPT: success={live_result['success']}")
    if not live_result['success']:
        logger.info(f"Error type: {live_result['error_type']}")
        logger.info(f"Error message: {live_result['error_message']}")
        logger.info("Traceback:")
        for line in live_result['traceback_lines']:
            logger.info(f"  {line}")
    
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC COMPLETE")
    logger.info("=" * 60)
    
    summary_text = f"""
DIAGNOSTIC SUMMARY FOR {TARGET_MODULE}
=====================================
Target path: {report['target_path']}
Exists: {report['exists']}
Total imports: {report['summary']['total_imports']}
Missing modules: {report['summary']['missing_modules']}
Syntax errors in dependencies: {report['summary']['syntax_errors']}

CRITICAL ERRORS:
{chr(10).join(f'  - {e}' for e in report['critical_errors']) if report['critical_errors'] else '  (none)'}

LIVE IMPORT:
  Success: {live_result['success']}
  Error type: {live_result['error_type'] or 'N/A'}
  Error message: {live_result['error_message'] or 'N/A'}

DIAGNOSTIC COMPLETED: {datetime.now(timezone.utc).isoformat()}
"""
    
    with open(LOG_FILE, 'a') as f:
        f.write(summary_text)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())