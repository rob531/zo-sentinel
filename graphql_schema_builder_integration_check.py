import ast
import os
import sys
from pathlib import Path
from typing import List, Set, Dict

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")


def find_files_with_mention(content: str, filename: str) -> bool:
    """Check if a specific filename is mentioned in content."""
    return filename in content


def scan_python_file_for_imports(filepath: Path) -> Set[str]:
    """Extract all import statements from a Python file."""
    imports = set()
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
    except (SyntaxError, ast.ASTError):
        pass
    return imports


def check_file_content(filepath: Path, search_term: str) -> List[str]:
    """Find lines containing search term."""
    matches = []
    try:
        with open(filepath, 'r') as f:
            for i, line in enumerate(f, 1):
                if search_term in line:
                    matches.append(f"  Line {i}: {line.strip()}")
    except Exception:
        pass
    return matches


def scan_directory_for_patterns(root: Path, pattern: str) -> Dict[str, List[str]]:
    """Scan all Python files for a pattern string."""
    results = {}
    for py_file in root.rglob("*.py"):
        if "graphql_schema_builder" in str(py_file):
            continue
        matches = check_file_content(py_file, pattern)
        if matches:
            results[str(py_file.relative_to(root))] = matches
    return results


def check_config_files(root: Path, target_name: str) -> Dict[str, List[str]]:
    """Check config files (yaml, json, ini, toml) for mentions."""
    results = {}
    config_extensions = {'.yaml', '.yml', '.json', '.ini', '.toml', '.conf'}
    for config_file in root.rglob("*"):
        if config_file.suffix in config_extensions:
            try:
                with open(config_file, 'r') as f:
                    content = f.read()
                    if target_name in content:
                        results[str(config_file.relative_to(root))] = [f"  Found '{target_name}' in config"]
            except Exception:
                pass
    return results


def check_route_registration(root: Path) -> Dict[str, List[str]]:
    """Check if graphql_schema_builder registers any routes."""
    results = {}
    graphql_file = root / "graphql_schema_builder.py"
    if graphql_file.exists():
        matches = check_file_content(graphql_file, "app.add_api_route")
        if matches:
            results["graphql_schema_builder.py"] = matches
        matches = check_file_content(graphql_file, "include_router")
        if matches:
            results["graphql_schema_builder.py"] = matches
        matches = check_file_content(graphql_file, "@app.get")
        if matches:
            results["graphql_schema_builder.py"] = matches
        matches = check_file_content(graphql_file, "@app.post")
        if matches:
            results["graphql_schema_builder.py"] = matches
    return results


def check_supervisor_configs(root: Path) -> Dict[str, List[str]]:
    """Check supervisor/daemon config files."""
    results = {}
    for config_file in root.rglob("*"):
        if config_file.name in ['supervisord.conf', 'supervisord.ini', 'daemon.conf']:
            try:
                with open(config_file, 'r') as f:
                    content = f.read()
                    if 'graphql_schema_builder' in content:
                        results[str(config_file.relative_to(root))] = check_file_content(config_file, 'graphql_schema_builder')
            except Exception:
                pass
    return results


def check_main_entry_points(root: Path) -> Dict[str, List[str]]:
    """Check main entry points for direct calls."""
    results = {}
    entry_points = ['main.py', 'run.py', 'server.py', '__main__.py']
    for ep in entry_points:
        for py_file in root.rglob(ep):
            matches = check_file_content(py_file, "graphql_schema_builder")
            if matches:
                results[str(py_file.relative_to(root))] = matches
    return results


def run_integration_check() -> bool:
    """Run the full graphql_schema_builder isolation check."""
    print("=" * 70)
    print("ZO-SENTINEL: GraphQL Schema Builder Isolation Check")
    print("=" * 70)
    
    all_checks_passed = True
    graphql_file = PROJECT_ROOT / "graphql_schema_builder.py"
    
    print("\n[CHECK 1] Existence of dormant graphql_schema_builder.py")
    if graphql_file.exists():
        print(f"  ✓ File exists: {graphql_file}")
        file_size = graphql_file.stat().st_size
        print(f"  ✓ File size: {file_size} bytes")
    else:
        print("  ⚠ File does not exist (may be removed in future)")
    
    print("\n[CHECK 2] Supervisor/Daemon Config Integration")
    supervisor_results = check_supervisor_configs(PROJECT_ROOT)
    if supervisor_results:
        all_checks_passed = False
        print("  ✗ FOUND graphql_schema_builder in supervisor configs:")
        for file, matches in supervisor_results.items():
            print(f"    {file}:")
            for m in matches:
                print(m)
    else:
        print("  ✓ No supervisor/daemon configs reference graphql_schema_builder")
    
    print("\n[CHECK 3] Main Entry Points (main.py, run.py, server.py)")
    entry_results = check_main_entry_points(PROJECT_ROOT)
    if entry_results:
        all_checks_passed = False
        print("  ✗ FOUND graphql_schema_builder in entry points:")
        for file, matches in entry_results.items():
            print(f"    {file}:")
            for m in matches:
                print(m)
    else:
        print("  ✓ No entry points reference graphql_schema_builder")
    
    print("\n[CHECK 4] Integration File References")
    integration_results = scan_directory_for_patterns(PROJECT_ROOT, "graphql_schema_builder")
    if integration_results:
        all_checks_passed = False
        print("  ✗ FOUND graphql_schema_builder in integration files:")
        for file, matches in integration_results.items():
            print(f"    {file}:")
            for m in matches:
                print(m)
    else:
        print("  ✓ No integration files reference graphql_schema_builder")
    
    print("\n[CHECK 5] Config Files (yaml, json, ini, toml)")
    config_results = check_config_files(PROJECT_ROOT, "graphql_schema_builder")
    if config_results:
        all_checks_passed = False
        print("  ✗ FOUND graphql_schema_builder in config files:")
        for file, matches in config_results.items():
            print(f"    {file}:")
            for m in matches:
                print(m)
    else:
        print("  ✓ No config files reference graphql_schema_builder")
    
    print("\n[CHECK 6] Route Registration Check")
    route_results = check_route_registration(PROJECT_ROOT)
    if route_results:
        all_checks_passed = False
        print("  ✗ FOUND active routes in graphql_schema_builder.py:")
        for file, matches in route_results.items():
            print(f"    {file}:")
            for m in matches:
                print(m)
    else:
        print("  ✓ No active route registration (add_api_route, include_router, @app.get/post)")
    
    print("\n" + "=" * 70)
    if all_checks_passed:
        print("RESULT: ✓ ALL CHECKS PASSED")
        print("graphql_schema_builder.py is confirmed dormant and isolated")
    else:
        print("RESULT: ✗ INTEGRATION VIOLATIONS FOUND")
        print("graphql_schema_builder.py may be wired into active components")
    print("=" * 70)
    
    return all_checks_passed


if __name__ == "__main__":
    success = run_integration_check()
    sys.exit(0 if success else 1)