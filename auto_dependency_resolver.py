import ast
import sys
import subprocess
import importlib.util
from typing import Dict
import requests

sys.path.insert(0, '/home/workspace/zo_mesh')

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'

def resolve_dependencies(filepath: str) -> Dict[str, str]:
    """
    Parse a Python file and auto-install missing third-party dependencies.
    
    Args:
        filepath: Path to the Python file to analyze
        
    Returns:
        Dict mapping module names to their install status:
        'installed' | 'already_present' | 'failed'
    """
    results = {}
    
    if not filepath:
        results['_error'] = 'No filepath provided'
        return results
    
    module_name = filepath
    actual_path = filepath
    
    if not filepath.endswith('.py'):
        spec = importlib.util.find_spec(filepath)
        if spec and spec.origin:
            actual_path = spec.origin
            module_name = filepath
        else:
            actual_path = filepath + '.py'
            module_name = filepath
    else:
        parts = filepath.replace('/', '.').split('.')
        if parts and not parts[-1]:
            parts = parts[:-1]
        module_name = '.'.join([p for p in parts if p and p != 'py'])
    
    try:
        with open(actual_path, 'r', encoding='utf-8') as f:
            code = f.read()
    except FileNotFoundError:
        results['_error'] = f'File not found: {actual_path}'
        return results
    except Exception as e:
        results['_error'] = str(e)
        return results
    
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        results['_error'] = f'Syntax error: {e}'
        return results
    
    modules = set()
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    root = alias.name.split('.')[0]
                    modules.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split('.')[0]
                modules.add(root)
            for alias in node.names:
                if alias.name:
                    modules.add(alias.name)
    
    stdlib = set(sys.stdlib_module_names)
    
    third_party = sorted(modules - stdlib)
    
    for mod in third_party:
        if not mod:
            continue
        
        spec = importlib.util.find_spec(mod)
        if spec is not None:
            results[mod] = 'already_present'
            continue
        
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--break-system-packages', mod],
                capture_output=True,
                text=True,
                timeout=120
            )
            if result.returncode == 0:
                results[mod] = 'installed'
            else:
                stderr = result.stderr.strip() if result.stderr else 'Unknown error'
                results[mod] = f'failed: {stderr[:200]}'
        except subprocess.TimeoutExpired:
            results[mod] = 'failed: timeout'
        except Exception as e:
            results[mod] = f'failed: {str(e)[:200]}'
    
    log_entry = {
        'event_type': 'dependency_resolution',
        'module_name': module_name,
        'filepath': actual_path,
        'results': str(results),
        'installed_count': sum(1 for v in results.values() if v == 'installed'),
        'already_present_count': sum(1 for v in results.values() if v == 'already_present'),
        'failed_count': sum(1 for v in results.values() if v.startswith('failed'))
    }
    
    try:
        requests.post(WRITE_SERVICE_URL, json={'table': 'mesh_events', 'rows': log_entry}, timeout=10)
    except Exception:
        pass
    
    return results

if __name__ == '__main__':
    if len(sys.argv) > 1:
        target = sys.argv[1]
        results = resolve_dependencies(target)
        print(f"Dependency resolution for: {target}")
        for module, status in results.items():
            print(f"  {module}: {status}")