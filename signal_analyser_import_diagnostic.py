#!/usr/bin/env python3
"""
Smoke-diagnostic utility to trace import chain failures in signal_analyser.py.
Does NOT modify any protected files. Pure read-only diagnostic.
"""

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple


def extract_imports_from_file(filepath: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """
    Parse a Python file and extract all import statements.
    
    Returns:
        Tuple of (regular_imports, from_imports)
        - regular_imports: list of module names from `import X` statements
        - from_imports: list of (module, full_name) tuples from `from X import Y` statements
    """
    regular_imports = []
    from_imports = []
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
    except FileNotFoundError:
        return [], []
    except Exception:
        return [], []
    
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                regular_imports.append(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            if node.module:
                module_name = node.module.split('.')[0]
                from_imports.append((module_name, node.module))
    
    return regular_imports, from_imports


def resolve_module(module_name: str) -> Dict:
    """
    Try to resolve a module using importlib.util.find_spec.
    
    Returns:
        Dict with 'found', 'name', 'path', and 'is_package' keys
    """
    result = {
        'name': module_name,
        'found': False,
        'path': None,
        'is_package': False
    }
    
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is not None:
            result['found'] = True
            if spec.origin:
                result['path'] = spec.origin
            elif spec.submodule_search_locations:
                result['path'] = spec.submodule_search_locations[0]
            result['is_package'] = spec.submodule_search_locations is not None
    except Exception:
        pass
    
    return result


def walk_package_imports(module_name: str, visited: Set[str]) -> List[Dict]:
    """
    Walk one level into a package to find its submodules.
    
    Returns:
        List of dicts for each importable submodule found
    """
    results = []
    
    if module_name in visited:
        return results
    
    visited.add(module_name)
    
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.submodule_search_locations is None:
        return results
    
    package_path = spec.submodule_search_locations[0]
    package_dir = Path(package_path)
    
    if not package_dir.is_dir():
        return results
    
    for item in package_dir.iterdir():
        if item.suffix == '.py' and item.stem != '__init__':
            submodule_name = f"{module_name}.{item.stem}"
            if submodule_name not in visited:
                sub_result = resolve_module(submodule_name)
                if sub_result['found']:
                    results.append(sub_result)
                    visited.add(submodule_name)
        elif item.is_dir() and (item / '__init__.py').exists():
            subpackage_name = f"{module_name}.{item.name}"
            if subpackage_name not in visited:
                sub_result = resolve_module(subpackage_name)
                if sub_result['found']:
                    results.append(sub_result)
                    visited.add(subpackage_name)
    
    return results


def diagnose_imports(filepath: str) -> Dict:
    """
    Main diagnostic function. Parses the file and checks all imports.
    
    Returns:
        JSON-serializable dict with 'importable', 'missing', and 'summary'
    """
    regular_imports, from_imports = extract_imports_from_file(filepath)
    
    importable = []
    missing = []
    visited = set()
    
    for module_name in regular_imports:
        if module_name in visited:
            continue
        
        result = resolve_module(module_name)
        
        if result['found']:
            importable.append(result)
            visited.add(module_name)
            
            if result['is_package']:
                sub_imports = walk_package_imports(module_name, visited)
                importable.extend(sub_imports)
        else:
            result['attempted_import'] = module_name
            missing.append(result)
            visited.add(module_name)
    
    for module_name, _ in from_imports:
        if module_name in visited:
            continue
        
        result = resolve_module(module_name)
        
        if result['found']:
            importable.append(result)
            visited.add(module_name)
            
            if result['is_package']:
                sub_imports = walk_package_imports(module_name, visited)
                importable.extend(sub_imports)
        else:
            result['attempted_import'] = module_name
            missing.append(result)
            visited.add(module_name)
    
    total_imports = len(importable) + len(missing)
    all_passed = len(missing) == 0
    
    summary = {
        'total_imports': total_imports,
        'importable_count': len(importable),
        'missing_count': len(missing),
        'status': 'PASS' if all_passed else 'FAIL'
    }
    
    return {
        'importable': importable,
        'missing': missing,
        'summary': summary
    }


if __name__ == '__main__':
    result = diagnose_imports('signal_analyser.py')
    
    assert 'importable' in result, "Report missing 'importable' key"
    assert 'missing' in result, "Report missing 'missing' key"
    assert 'summary' in result, "Report missing 'summary' key"
    
    print(json.dumps(result, indent=2))
    
    sys.exit(0 if result['summary']['status'] == 'PASS' else 1)