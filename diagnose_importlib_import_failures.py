#!/usr/bin/env python3
import json
import sys
import traceback
from pathlib import Path

PROTECTED_FILES = ['registry_api.py', 'rug_pull_monitor.py', 'signal_analyser.py']

def diagnose_file(filepath):
    """Attempt to import a file and identify failing imports."""
    result = {
        'file': filepath.name,
        'failing_import': None,
        'likely_cause': None,
        'recommendation': None,
        'import_errors': []
    }
    
    if not filepath.exists():
        result['likely_cause'] = 'file_not_found'
        result['recommendation'] = 'File does not exist at specified path'
        return result
    
    content = filepath.read_text()
    
    # Parse import lines
    import_lines = []
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            import_lines.append(stripped)
    
    result['import_lines'] = import_lines
    
    # Try to compile the file to check for syntax errors
    try:
        compile(content, str(filepath), 'exec')
        result['syntax_valid'] = True
    except SyntaxError as e:
        result['syntax_valid'] = False
        result['likely_cause'] = f'syntax_error: {e.msg} at line {e.lineno}'
        result['recommendation'] = 'Fix syntax error before importing'
        return result
    
    result['syntax_valid'] = True
    
    # Test each import individually
    for imp in import_lines:
        try:
            if imp.startswith('from '):
                # Parse: from module import name
                parts = imp.split(' import ')
                module = parts[0].replace('from ', '')
                names = parts[1].split(', ') if len(parts) > 1 else ['*']
                for name in names:
                    name = name.strip()
                    if name == '*':
                        __import__(module)
                    else:
                        mod = __import__(module, fromlist=[name])
                        getattr(mod, name)
            else:
                # Parse: import module[ as alias]
                module = imp.replace('import ', '').split(' as ')[0].strip()
                __import__(module)
        except ImportError as e:
            result['import_errors'].append({
                'import_statement': imp,
                'error': str(e),
                'error_type': 'ImportError'
            })
        except AttributeError as e:
            result['import_errors'].append({
                'import_statement': imp,
                'error': str(e),
                'error_type': 'AttributeError'
            })
        except ModuleNotFoundError as e:
            result['import_errors'].append({
                'import_statement': imp,
                'error': str(e),
                'error_type': 'ModuleNotFoundError'
            })
        except Exception as e:
            result['import_errors'].append({
                'import_statement': imp,
                'error': str(e),
                'error_type': type(e).__name__
            })
    
    # Determine primary failure
    if result['import_errors']:
        primary_error = result['import_errors'][0]
        result['failing_import'] = primary_error['import_statement']
        result['likely_cause'] = f"{primary_error['error_type']}: {primary_error['error']}"
        
        if primary_error['error_type'] == 'ModuleNotFoundError':
            mod_name = primary_error['error'].split("'")[1] if "'" in primary_error['error'] else 'unknown'
            result['recommendation'] = f'Install missing module: pip install {mod_name}'
        elif primary_error['error_type'] == 'ImportError':
            result['recommendation'] = 'Check for circular imports or missing dependencies in sys.path'
        elif primary_error['error_type'] == 'AttributeError':
            result['recommendation'] = 'Verify the module exports the expected attribute name'
        else:
            result['recommendation'] = 'Investigate dependency chain and module resolution'
    else:
        result['likely_cause'] = 'no_import_failure_detected'
        result['recommendation'] = 'File imports successfully - check runtime initialization'
    
    return result

def main():
    base_path = Path('/home/workspace/zo_sentinel')
    files_to_check = [
        base_path / 'registry_api.py',
        base_path / 'rug_pull_monitor.py', 
        base_path / 'signal_analyser.py'
    ]
    
    results = []
    for filepath in files_to_check:
        result = diagnose_file(filepath)
        results.append(result)
        print(json.dumps(result, indent=2))
        print("---")
    
    # Summary
    summary = {
        'summary': {
            'files_checked': len(files_to_check),
            'files_with_failures': sum(1 for r in results if r['failing_import']),
            'failed_files': [r['file'] for r in results if r['failing_import']]
        }
    }
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()