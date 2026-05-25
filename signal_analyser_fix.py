import sys
import importlib
import traceback
import ast
import inspect
from pathlib import Path
from typing import Dict, Any, Optional

SIGNAL_ANALYSER_PATH = Path("/home/workspace/zo_sentinel/signal_analyser.py")

def check_syntax_errors(module_path: Path) -> Optional[str]:
    """Check for Python syntax errors without importing."""
    try:
        with open(module_path, 'r') as f:
            source = f.read()
        ast.parse(source)
        return None
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}: {e.msg}"
    except Exception as e:
        return f"File read error: {e}"

def check_import_issues(module_path: Path) -> Dict[str, Any]:
    """Analyze imports without triggering circular dependencies."""
    missing_imports = []
    circular_suspects = []
    
    try:
        with open(module_path, 'r') as f:
            source = f.read()
        tree = ast.parse(source)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    try:
                        if alias.name.startswith('.'):
                            continue
                        parts = alias.name.split('.')
                        root_module = parts[0]
                        if root_module not in sys.modules:
                            try:
                                importlib.import_module(alias.name)
                            except ImportError:
                                missing_imports.append(alias.name)
                    except Exception:
                        pass
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    try:
                        if node.module.startswith('.'):
                            continue
                        importlib.import_module(node.module)
                    except ImportError:
                        missing_imports.append(node.module)
                        
    except SyntaxError:
        pass
    except Exception as e:
        circular_suspects.append(f"Import analysis error: {e}")
    
    return {
        'missing_imports': missing_imports,
        'circular_suspects': circular_suspects
    }

def import_module_safely(module_name: str) -> tuple[bool, Optional[str], Optional[Exception]]:
    """Attempt to import module and capture any errors."""
    try:
        if module_name in sys.modules:
            mod = sys.modules[module_name]
        else:
            mod = importlib.import_module(module_name)
        return True, mod, None
    except ImportError as e:
        return False, None, e
    except Exception as e:
        return False, None, e

def diagnose_signal_analyser() -> Dict[str, Any]:
    """Main diagnostic function for signal_analyser smoke failure."""
    diagnosis = {
        'module_path': str(SIGNAL_ANALYSER_PATH),
        'exists': SIGNAL_ANALYSER_PATH.exists(),
        'syntax_errors': None,
        'import_issues': {},
        'import_result': None,
        'import_error': None,
        'full_traceback': None,
        'likely_causes': [],
        'recommendations': []
    }
    
    if not diagnosis['exists']:
        diagnosis['likely_causes'].append('Module file does not exist')
        diagnosis['recommendations'].append('Create signal_analyser.py')
        return diagnosis
    
    diagnosis['syntax_errors'] = check_syntax_errors(SIGNAL_ANALYSER_PATH)
    if diagnosis['syntax_errors']:
        diagnosis['likely_causes'].append('Python syntax error in module')
        diagnosis['recommendations'].append('Fix syntax errors listed above')
    
    diagnosis['import_issues'] = check_import_issues(SIGNAL_ANALYSER_PATH)
    if diagnosis['import_issues']['missing_imports']:
        diagnosis['likely_causes'].append('Missing required imports')
    
    success, module, error = import_module_safely('signal_analyser')
    diagnosis['import_result'] = success
    
    if not success:
        diagnosis['import_error'] = str(error)
        diagnosis['full_traceback'] = traceback.format_exc()
        
        error_str = str(error).lower()
        if 'circular' in error_str:
            diagnosis['likely_causes'].append('Circular import dependency detected')
            diagnosis['recommendations'].append('Reorder imports or use late binding')
        elif 'no module named' in error_str or 'cannot import name' in error_str:
            diagnosis['likely_causes'].append('Missing import (runtime)')
            missing = error_str.split("'")[-2] if "'" in error_str else 'unknown'
            diagnosis['recommendations'].append(f'Install or add missing dependency: {missing}')
        elif 'syntax' in error_str:
            diagnosis['likely_causes'].append('Syntax error prevents import')
        else:
            diagnosis['likely_causes'].append('Unknown import error')
    
    if success and module:
        try:
            functions = [name for name, obj in inspect.getmembers(module) 
                        if inspect.isfunction(obj) and not name.startswith('_')]
            classes = [name for name, obj in inspect.getmembers(module) 
                      if inspect.isclass(obj) and not name.startswith('_')]
            diagnosis['detected_functions'] = functions
            diagnosis['detected_classes'] = classes
        except Exception as e:
            diagnosis['recommendations'].append(f'Could not inspect module: {e}')
    
    return diagnosis

def print_diagnosis(diagnosis: Dict[str, Any]) -> None:
    """Pretty print the diagnosis results."""
    print("=" * 60)
    print("SIGNAL_ANALYSER SMOKE FAILURE DIAGNOSTIC")
    print("=" * 60)
    print(f"Module Path: {diagnosis['module_path']}")
    print(f"Exists: {diagnosis['exists']}")
    print()
    
    if diagnosis['syntax_errors']:
        print("SYNTAX ERRORS:")
        print(f"  {diagnosis['syntax_errors']}")
        print()
    
    if diagnosis['import_issues']['missing_imports']:
        print("MISSING IMPORTS (static analysis):")
        for imp in diagnosis['import_issues']['missing_imports']:
            print(f"  - {imp}")
        print()
    
    print(f"Import Successful: {diagnosis['import_result']}")
    if diagnosis['import_error']:
        print(f"Import Error: {diagnosis['import_error']}")
        print()
        print("FULL TRACEBACK:")
        print(diagnosis['full_traceback'])
        print()
    
    if diagnosis['likely_causes']:
        print("LIKELY CAUSES:")
        for cause in diagnosis['likely_causes']:
            print(f"  - {cause}")
        print()
    
    if diagnosis['recommendations']:
        print("RECOMMENDATIONS:")
        for rec in diagnosis['recommendations']:
            print(f"  - {rec}")
        print()
    
    if diagnosis.get('detected_functions'):
        print(f"Detected Functions: {', '.join(diagnosis['detected_functions'])}")
    if diagnosis.get('detected_classes'):
        print(f"Detected Classes: {', '.join(diagnosis['detected_classes'])}")
    
    print("=" * 60)

def run() -> int:
    """Main entry point for diagnostic."""
    diagnosis = diagnose_signal_analyser()
    print_diagnosis(diagnosis)
    
    if diagnosis['import_result']:
        print("\n[OK] signal_analyser imports successfully")
        return 0
    else:
        print("\n[FAIL] signal_analyser has issues - see above")
        return 1

if __name__ == '__main__':
    sys.exit(run())