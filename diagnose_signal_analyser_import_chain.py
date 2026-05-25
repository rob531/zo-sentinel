#!/usr/bin/env python3
"""
diagnose_signal_analyser_import_chain.py
Traces import dependencies and detects circular/broken imports.
"""

import sys
import os
import ast
import importlib
import traceback
from pathlib import Path
from collections import defaultdict, deque

PROJECT_ROOT = Path('/home/workspace/zo_sentinel')
sys.path.insert(0, str(PROJECT_ROOT))

def parse_imports(filepath):
    """Extract all import statements from a Python file."""
    imports = {'absolute': [], 'relative': [], 'from_imports': []}
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports['absolute'].append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                level = node.level
                for alias in node.names:
                    imports['from_imports'].append({
                        'module': module,
                        'level': level,
                        'name': alias.name,
                        'asname': alias.asname
                    })
    except Exception as e:
        imports['error'] = str(e)
    return imports

def resolve_module_path(module_name, base_path):
    """Try to resolve a module name to a file path."""
    parts = module_name.split('.')
    candidates = [
        base_path / '/'.join(parts) + '.py',
        base_path / '/'.join(parts) / '__init__.py',
        Path('/home/workspace/zo_sentinel') / '/'.join(parts) + '.py',
        Path('/home/workspace/zo_sentinel') / '/'.join(parts) / '__init__.py',
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None

def build_import_graph(start_file):
    """Build a directed graph of import dependencies."""
    graph = defaultdict(list)
    visited = set()
    unresolved = []
    
    def visit(filepath, name=None):
        if filepath in visited:
            return
        visited.add(filepath)
        
        imports = parse_imports(filepath)
        if 'error' in imports:
            unresolved.append((filepath, imports['error']))
            return
        
        for imp in imports['absolute']:
            imp_path = resolve_module_path(imp, Path(filepath).parent)
            if imp_path:
                graph[filepath].append((imp, imp_path))
                if imp_path not in visited:
                    visit(imp_path, imp)
            else:
                graph[filepath].append((imp, None))
        
        for item in imports['from_imports']:
            level = item['level']
            module = item['module']
            name = item['name']
            
            if level > 0:
                base = str(Path(filepath).parent)
                for _ in range(level - 1):
                    base = str(Path(base).parent)
                if module:
                    path = Path(base) / module.replace('.', '/')
                else:
                    path = Path(base)
            else:
                if module:
                    path = resolve_module_path(module, Path(filepath).parent)
                else:
                    path = None
            
            if path:
                graph[filepath].append((f"{module}.{name}" if module else name, str(path)))
            else:
                graph[filepath].append((f"{module}.{name}" if module else name, None))
    
    visit(str(start_file))
    return graph, unresolved

def check_circular_imports(graph):
    """Detect circular import chains using DFS."""
    cycles = []
    white = set(graph.keys())
    gray = set()
    parent = {}
    
    def dfs(node, path):
        white.discard(node)
        gray.add(node)
        for target, target_path in graph.get(node, []):
            if target_path is None:
                continue
            if target_path in gray:
                cycle_start = path.index(target_path)
                cycle = path[cycle_start:] + [target_path]
                cycles.append(cycle)
            elif target_path in white:
                parent[target_path] = node
                dfs(target_path, path + [target_path])
        gray.discard(node)
    
    for node in list(white):
        dfs(node, [node])
    
    return cycles

def test_import_chain(file_path):
    """Attempt to import and capture all failures."""
    results = {
        'success': False,
        'errors': [],
        'traceback': None
    }
    
    try:
        importlib.invalidate_caches()
        
        module_name = None
        rel_path = Path(file_path).relative_to(PROJECT_ROOT)
        module_name = str(rel_path).replace('/', '.').replace('.py', '')
        
        mod = importlib.import_module(module_name)
        results['success'] = True
    except Exception as e:
        results['errors'].append({
            'type': type(e).__name__,
            'message': str(e),
            'module': getattr(e, 'name', None),
            'path': getattr(e, 'path', None)
        })
        results['traceback'] = traceback.format_exc()
    
    return results

def main():
    print("=" * 70)
    print("ZO-SENTINEL: Signal Analyser Import Chain Diagnostic")
    print("=" * 70)
    
    signal_analyser_path = PROJECT_ROOT / 'services' / 'signal_analyser.py'
    registry_api_path = PROJECT_ROOT / 'services' / 'registry_api.py'
    
    print(f"\n[1] ANALYSING: signal_analyser.py")
    print("-" * 50)
    
    if not signal_analyser_path.exists():
        print(f"ERROR: {signal_analyser_path} not found")
        return
    
    print("\n--- Import statements found ---")
    imports = parse_imports(str(signal_analyser_path))
    for imp in imports['absolute']:
        print(f"  import {imp}")
    for item in imports['from_imports']:
        if item['level'] > 0:
            print(f"  from {'.' * item['level']}{item['module']} import {item['name']}")
        elif item['module']:
            print(f"  from {item['module']} import {item['name']}")
        else:
            print(f"  import {item['name']}")
    
    print("\n--- Building dependency graph ---")
    graph, unresolved = build_import_graph(signal_analyser_path)
    
    print("\nDependency edges:")
    for source, targets in sorted(graph.items()):
        source_rel = Path(source).relative_to(PROJECT_ROOT)
        for target, target_path in targets:
            status = "✓" if target_path else "✗ UNRESOLVED"
            print(f"  {source_rel} → {target} {status}")
    
    if unresolved:
        print("\n--- Parse errors ---")
        for path, error in unresolved:
            print(f"  {Path(path).relative_to(PROJECT_ROOT)}: {error}")
    
    print("\n--- Circular import detection ---")
    cycles = check_circular_imports(graph)
    if cycles:
        for i, cycle in enumerate(cycles, 1):
            print(f"  Cycle #{i}:")
            for node in cycle:
                print(f"    → {Path(node).relative_to(PROJECT_ROOT)}")
    else:
        print("  No circular imports detected (may still exist at runtime)")
    
    print("\n[2] ANALYSING: registry_api.py")
    print("-" * 50)
    
    if registry_api_path.exists():
        reg_imports = parse_imports(str(registry_api_path))
        print("\n--- Import statements ---")
        for imp in reg_imports['absolute']:
            print(f"  import {imp}")
        for item in reg_imports['from_imports']:
            if item['level'] > 0:
                print(f"  from {'.' * item['level']}{item['module']} import {item['name']}")
            elif item['module']:
                print(f"  from {item['module']} import {item['name']}")
            else:
                print(f"  import {item['name']}")
        
        print("\n--- Import test (runtime) ---")
        result = test_import_chain(registry_api_path)
        if result['success']:
            print("  ✓ registry_api imports successfully")
        else:
            print(f"  ✗ Import failed: {result['errors'][0]['message']}")
            print("\n--- Traceback ---")
            print(result['traceback'])
    
    print("\n[3] SIGNAL ANALYSER RUNTIME IMPORT TEST")
    print("-" * 50)
    result = test_import_chain(signal_analyser_path)
    
    if result['success']:
        print("  ✓ signal_analyser imports successfully")
    else:
        print("  ✗ Import failed")
        for err in result['errors']:
            print(f"\n  Error Type: {err['type']}")
            print(f"  Message: {err['message']}")
            if err['module']:
                print(f"  Failed Module: {err['module']}")
        print("\n--- Full Traceback ---")
        print(result['traceback'])
    
    print("\n[4] ROOT CAUSE ANALYSIS")
    print("-" * 50)
    
    if not result['success'] and result['traceback']:
        tb = result['traceback']
        
        missing_deps = []
        circular_patterns = []
        
        for line in tb.split('\n'):
            if 'ModuleNotFoundError' in line or 'ImportError' in line:
                missing_deps.append(line.strip())
            if 'circular' in line.lower() or 'cycle' in line.lower():
                circular_patterns.append(line.strip())
        
        if missing_deps:
            print("  Missing Dependencies Detected:")
            for dep in missing_deps:
                print(f"    • {dep}")
        
        if circular_patterns:
            print("  Circular Import Patterns:")
            for pattern in circular_patterns:
                print(f"    • {pattern}")
        
        # Extract which module fails first
        lines = tb.split('\n')
        for i, line in enumerate(lines):
            if 'File "' in line and line.endswith('.py")'):
                print(f"\n  First import failure: {line}")
                if i + 1 < len(lines):
                    print(f"  At: {lines[i+1].strip()}")
                break
    
    print("\n[5] RECOMMENDATIONS (diagnostic only)")
    print("-" * 50)
    
    print("""
  To fix import chain issues:
  
  1. For ModuleNotFoundError:
     - Check sys.path includes the module directory
     - Verify package __init__.py files exist
     - Ensure no typos in import statements
  
  2. For circular imports:
     - Move imports inside functions (deferred imports)
     - Use importlib for lazy loading
     - Break up shared utility modules
  
  3. For attribute errors at import time:
     - Check module initialization code for runtime deps
     - Move heavy initialization to __init__ methods
""")
    
    print("=" * 70)
    print("Diagnostic complete. Output saved.")
    print("=" * 70)

if __name__ == '__main__':
    main()