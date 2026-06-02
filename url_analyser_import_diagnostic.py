#!/usr/bin/env python3
"""
url_analyser_import_diagnostic.py

A read-only diagnostic utility that introspects the import chain of url_analyser
without modifying it. Diagnoses why url_analyser triggers import errors across
cohort builds by verifying all transitive dependencies are resolvable.
"""

import importlib.util
import ast
from datetime import datetime, timezone
from typing import Set, List, Dict, Any


class ImportVisitor(ast.NodeVisitor):
    """AST visitor that extracts import statements without executing code."""
    
    def __init__(self):
        self.imports: Set[str] = set()
    
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name.split('.')[0])
    
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0:  # Not a relative import
            if node.module:
                self.imports.add(node.module.split('.')[0])
    
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Skip type annotations to avoid false positives
        pass


def get_source_file_path(module_name: str) -> str | None:
    """Find the path to a module's source file."""
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return None
    if spec.submodule_search_locations:
        # It's a package - look for __init__.py
        for location in spec.submodule_search_locations:
            init_path = f"{location}/__init__.py"
            try:
                with open(init_path, 'r', encoding='utf-8'):
                    return init_path
            except (OSError, IOError):
                pass
    if spec.origin and spec.origin.endswith('.py'):
        return spec.origin
    return None


def extract_imports_from_source(source: str) -> Set[str]:
    """Parse source code to extract import statements."""
    try:
        tree = ast.parse(source, type_comments=True)
        visitor = ImportVisitor()
        visitor.visit(tree)
        return visitor.imports
    except SyntaxError:
        return set()


def traverse_imports(target: str, max_depth: int = 50) -> Dict[str, Any]:
    """
    Traverse the full transitive import graph of a module.
    
    Args:
        target: The target module name to analyze
        max_depth: Maximum recursion depth to prevent infinite loops
    
    Returns:
        Dictionary containing the diagnostic report
    """
    all_modules: Set[str] = set()
    importable: Set[str] = set()
    missing: Set[str] = set()
    visited: Set[str] = set()
    queue: List[tuple] = [(target, 0)]  # (module_name, depth)
    
    while queue:
        current, depth = queue.pop(0)
        
        if current in visited or depth > max_depth:
            continue
        
        visited.add(current)
        
        if current in all_modules:
            continue
        
        all_modules.add(current)
        
        # Check if module is resolvable
        spec = importlib.util.find_spec(current)
        if spec is not None:
            importable.add(current)
        else:
            missing.add(current)
            continue  # Skip traversal of missing modules
        
        # Extract imports from source
        source_path = get_source_file_path(current)
        if source_path:
            try:
                with open(source_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                imports = extract_imports_from_source(source)
                
                for imp in imports:
                    if imp and imp not in visited:
                        queue.append((imp, depth + 1))
            except (OSError, IOError):
                pass
    
    return {
        'target_module': target,
        'all_modules': sorted(list(all_modules)),
        'importable': sorted(list(importable)),
        'missing': sorted(list(missing))
    }


def generate_report(target: str) -> Dict[str, Any]:
    """Generate a complete diagnostic report for a target module."""
    traversal = traverse_imports(target)
    
    return {
        'target_module': target,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'all_modules': traversal['all_modules'],
        'importable': traversal['importable'],
        'missing': traversal['missing'],
        'summary': {
            'total': len(traversal['all_modules']),
            'importable': len(traversal['importable']),
            'missing': len(traversal['missing'])
        }
    }


def diagnose(target: str = 'url_analyser') -> Dict[str, Any]:
    """
    Main diagnostic function.
    
    Args:
        target: Module name to diagnose (default: 'url_analyser')
    
    Returns:
        Complete diagnostic report as a dictionary
    """
    return generate_report(target)


def print_report(report: Dict[str, Any], verbose: bool = False) -> None:
    """Print the diagnostic report to stdout."""
    print(json.dumps(report, indent=2))
    
    if verbose:
        print("\n" + "=" * 60)
        print("DIAGNOSTIC SUMMARY")
        print("=" * 60)
        print(f"Target Module: {report['target_module']}")
        print(f"Timestamp: {report['timestamp']}")
        print(f"\nTotal Modules Discovered: {report['summary']['total']}")
        print(f"Successfully Importable: {report['summary']['importable']}")
        print(f"Missing/Unresolvable: {report['summary']['missing']}")
        
        if report['missing']:
            print(f"\nMISSING MODULES (causing import errors):")
            for m in report['missing']:
                print(f"  - {m}")


def main() -> int:
    """CLI entry point."""
    import json
    
    target = sys.argv[1] if len(sys.argv) > 1 else 'url_analyser'
    
    report = diagnose(target)
    
    if report['summary']['total'] == 0:
        print(f"Error: Could not find module '{target}'", file=sys.stderr)
        return 1
    
    print_report(report, verbose=True)
    return 0


if __name__ == '__main__':
    import sys
    
    target = sys.argv[1] if len(sys.argv) > 1 else 'url_analyser'
    
    print(f"Diagnosing import chain for: {target}")
    print("-" * 50)
    
    report = diagnose(target)
    
    print_report(report, verbose=True)
    
    # Exit code indicates health
    if report['missing']:
        sys.exit(1)
    sys.exit(0)