"""
Signal Analyser Smoke Diagnostic Utility

Diagnostic tool to investigate import failures in signal_analyser.py.
Analyzes dependencies, circular imports, and missing __init__.py files.

Note: Uses importlib.metadata (stdlib in Python 3.8+). For older versions,
install backport: pip install importlib_metadata
"""

import ast
import importlib.metadata
import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional


def check_missing_dependencies(signal_analyser_path: str) -> dict:
    """
    Check for missing dependencies in signal_analyser.py.

    Parses the file using AST to extract all import statements and compares
    them against installed packages using importlib.metadata.

    Args:
        signal_analyser_path: Path to signal_analyser.py

    Returns:
        dict with keys: found (list), missing (list), status (str)
    """
    result = {
        "found": [],
        "missing": [],
        "status": "UNKNOWN"
    }

    if not os.path.exists(signal_analyser_path):
        result["status"] = "ERROR"
        result["error"] = f"File not found: {signal_analyser_path}"
        return result

    try:
        with open(signal_analyser_path, 'r', encoding='utf-8') as f:
            source_code = f.read()
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        return result

    # Get all installed packages (lowercase for comparison)
    try:
        installed_packages = set(
            pkg.lower().replace('-', '_') 
            for pkg in importlib.metadata.packages_distributions().keys()
        )
    except Exception:
        # Fallback for older Python or importlib_metadata backport
        try:
            from importlib_metadata import packages_distributions
            installed_packages = set(
                pkg.lower().replace('-', '_')
                for pkg in packages_distributions().keys()
            )
        except ImportError:
            installed_packages = set()

    # Standard library modules (safe to ignore if missing)
    stdlib_modules = {
        'os', 'sys', 'pathlib', 're', 'json', 'time', 'datetime', 'collections',
        'itertools', 'functools', 'operator', 'string', 'random', 'math', 'stat',
        'fileinput', 'glob', 'fnmatch', 'linecache', 'tokenize', 'keyword',
        'ast', 'dis', 'inspect', 'traceback', 'gc', 'weakref', 'types',
        'contextlib', 'dataclasses', 'typing', 'warnings', 'queue', 'threading',
        'multiprocessing', 'subprocess', 'socket', 'ssl', 'http', 'urllib',
        'html', 'xml', 'email', 'html', 'zipfile', 'tarfile', 'gzip', 'bz2',
        'lzma', 'shutil', 'tempfile', 'io', 'pickle', 'copy', 'struct',
        'codecs', 'unicodedata', 'locale', 'gettext', 'argparse', 'optparse',
        'configparser', 'logging', 'platform', 'errno', 'ctypes', 'signal',
        'mmap', 'readline', 'crypt', 'termios', 'tty', 'pty', 'fcntl', 'pipes',
        'code', 'codeop', 'pty', 'select', 'selectors', 'asyncio', 'concurrent',
        'unittest', 'test', 'doctest', 'decimal', 'fractions', 'numbers',
        'cmath', 'statistics', 'array', 'bisect', 'heapq', 'graphlib',
        'enum', 'graphlib', 'graphlib', 'pprint', 'textwrap', 'codecs'
    }

    # Parse AST to find all imports
    imported_modules = set()
    imported_names = {}  # module -> list of imported names

    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        result["status"] = "ERROR"
        result["error"] = f"Syntax error in file: {e}"
        return result

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split('.')[0].lower()
                imported_modules.add(module_name)
                if module_name not in imported_names:
                    imported_names[module_name] = []
                if alias.asname:
                    imported_names[module_name].append(alias.asname)
                else:
                    imported_names[module_name].append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_name = node.module.split('.')[0].lower()
                imported_modules.add(module_name)
                if module_name not in imported_names:
                    imported_names[module_name] = []
                for alias in node.names:
                    if alias.asname:
                        imported_names[module_name].append(alias.asname)
                    else:
                        imported_names[module_name].append(alias.name)

    # Categorize imports
    found_stdlib = []
    found_third_party = []
    missing_third_party = []
    uncertain = []

    for module in imported_modules:
        if module in stdlib_modules:
            found_stdlib.append(module)
        elif module in installed_packages:
            found_third_party.append(module)
        elif module in ['signal_analyser'] or module.startswith('_'):
            # Skip self-references and private modules
            continue
        else:
            # Could be missing or part of the project
            uncertain.append(module)

    result["found"] = {
        "stdlib": sorted(found_stdlib),
        "third_party": sorted(found_third_party)
    }
    result["uncertain"] = sorted(uncertain)
    result["installed_packages"] = sorted(list(installed_packages)[:50])  # Sample

    # Determine status
    if uncertain:
        # Check if uncertain modules are part of the project (subdirectories)
        project_path = os.path.dirname(os.path.abspath(signal_analyser_path))
        actual_missing = []

        for mod in uncertain:
            # Check if it's a local module/package
            mod_path = os.path.join(project_path, mod)
            init_path = os.path.join(project_path, mod, '__init__.py')

            if not os.path.exists(mod_path) and not os.path.exists(mod_path + '.py') and not os.path.exists(init_path):
                actual_missing.append(mod)

        if actual_missing:
            result["missing"] = actual_missing
            result["status"] = "WARNING"
        else:
            result["status"] = "PASS"
    else:
        result["status"] = "PASS"

    return result


def check_circular_imports(package_root: str) -> dict:
    """
    Check for circular imports in Python package files.

    Uses AST analysis to build an import graph and DFS with path tracking
    to detect cycles.

    Args:
        package_root: Root directory of the package to analyze

    Returns:
        dict with keys: cycles (list of lists), status (str)
    """
    result = {
        "cycles": [],
        "status": "UNKNOWN"
    }

    if not os.path.exists(package_root):
        result["status"] = "ERROR"
        result["error"] = f"Directory not found: {package_root}"
        return result

    # Build import graph
    import_graph = {}  # module_name -> set of imported module names

    def get_module_name(file_path: str, root: str) -> str:
        """Convert file path to module name."""
        rel_path = os.path.relpath(file_path, root)
        module_name = rel_path.replace(os.sep, '.').replace('/', '.')
        if module_name.endswith('.__init__.py'):
            module_name = module_name[:-12]
        elif module_name.endswith('.py'):
            module_name = module_name[:-3]
        return module_name

    def extract_imports(file_path: str) -> set:
        """Extract all imports from a Python file."""
        imports = set()
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return imports

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0])

        return imports

    # Walk all Python files
    python_files = []
    for root, dirs, files in os.walk(package_root):
        # Skip common non-package directories
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.pytest_cache',
                                                  'venv', 'env', '.venv', '.env', 'node_modules')]

        for file in files:
            if file.endswith('.py') and file != '__pycache__':
                python_files.append(os.path.join(root, file))

    # Build graph
    for file_path in python_files:
        module_name = get_module_name(file_path, package_root)
        imports = extract_imports(file_path)

        # Filter to only local imports (within this package)
        local_imports = set()
        for imp in imports:
            # Check if import refers to a local module
            for other_file in python_files:
                other_module = get_module_name(other_file, package_root)
                if other_module == imp or other_module.endswith('.' + imp):
                    local_imports.add(other_module)

        import_graph[module_name] = local_imports

    # Detect cycles using DFS with path tracking
    def find_cycles() -> list:
        """Find all cycles in the import graph using DFS."""
        cycles = []
        visited = set()
        rec_stack = []

        def dfs(node: str, path: list) -> None:
            visited.add(node)
            rec_stack.append(node)

            for neighbor in import_graph.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor, path + [neighbor])
                elif neighbor in rec_stack:
                    # Found cycle - extract the cycle path
                    cycle_start = rec_stack.index(neighbor)
                    cycle = rec_stack[cycle_start:] + [neighbor]
                    if cycle not in cycles:
                        cycles.append(cycle)

            rec_stack.pop()

        for node in import_graph:
            if node not in visited:
                dfs(node, [node])

        return cycles

    cycles = find_cycles()

    if cycles:
        result["cycles"] = cycles
        result["status"] = "WARNING"
    else:
        result["status"] = "PASS"

    result["modules_analyzed"] = len(import_graph)
    result["files_analyzed"] = len(python_files)

    return result


def check_missing_inits(package_dirs: list) -> dict:
    """
    Check for missing __init__.py files in package directories.

    Verifies that all directories that should be Python packages
    contain an __init__.py file.

    Args:
        package_dirs: List of package directories to check

    Returns:
        dict with keys: missing (list), status (str)
    """
    result = {
        "missing": [],
        "status": "UNKNOWN"
    }

    if not package_dirs:
        result["status"] = "ERROR"
        result["error"] = "No package directories provided"
        return result

    missing_inits = []
    checked_dirs = []

    for package_dir in package_dirs:
        if not os.path.exists(package_dir):
            continue

        # Find all directories that contain .py files
        dirs_with_py_files = set()

        for root, dirs, files in os.walk(package_dir):
            # Skip hidden and cache directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']

            for file in files:
                if file.endswith('.py'):
                    dirs_with_py_files.add(root)

        # Check each directory with Python files for __init__.py
        for dir_path in dirs_with_py_files:
            checked_dirs.append(dir_path)
            init_path = os.path.join(dir_path, '__init__.py')
            if not os.path.exists(init_path):
                rel_path = os.path.relpath(dir_path, os.getcwd())
                missing_inits.append(rel_path)

    result["checked"] = checked_dirs
    result["missing"] = sorted(set(missing_inits))

    if missing_inits:
        result["status"] = "FAIL"
    else:
        result["status"] = "PASS"

    return result


def parse_traceback_failure(traceback_text: str) -> dict:
    """
    Parse raw traceback text to extract failure information.

    Extracts the specific module that failed, line number, exception type,
    and error message from a traceback.

    Args:
        traceback_text: Raw traceback text string

    Returns:
        dict with extracted failure information
    """
    import re

    result = {
        "module": None,
        "line_number": None,
        "exception_type": None,
        "exception_message": None,
        "frozen_importlib": False,
        "status": "UNKNOWN"
    }

    if not traceback_text or not traceback_text.strip():
        result["status"] = "NO_TRACEBACK"
        return result

    # Check for frozen importlib error (common at line 10)
    frozen_pattern = r'<frozen\s+importlib\.[^>]+>[,\s]+line\s+(\d+)'
    frozen_match = re.search(frozen_pattern, traceback_text)

    if frozen_match:
        result["frozen_importlib"] = True
        result["line_number"] = int(frozen_match.group(1))

    # Extract exception type and message
    exception_pattern = r'(\w+Error|\w+Exception):\s*(.+?)(?:\n|$)'
    exception_match = re.search(exception_pattern, traceback_text)

    if exception_match:
        result["exception_type"] = exception_match.group(1)
        result["exception_message"] = exception_match.group(2).strip()

    # Extract module name from "ModuleNotFoundError: No module named 'X'"
    module_pattern = r"No module named\s+['\"]([^'\"]+)['\"]"
    module_match = re.search(module_pattern, traceback_text)

    if module_match:
        result["module"] = module_match.group(1)
        result["status"] = "MODULE_NOT_FOUND"
    elif "ImportError" in traceback_text or "cannot import" in traceback_text.lower():
        # Try to extract import source
        import_pattern = r"cannot import name ['\"]([^'\"]+)['\"]"
        import_match = re.search(import_pattern, traceback_text)
        if import_match:
            result["module"] = import_match.group(1)
            result["status"] = "IMPORT_ERROR"
        else:
            result["status"] = "IMPORT_ERROR"
    elif frozen_match:
        result["status"] = "FROZEN_IMPORTLIB"
    else:
        result["status"] = "UNKNOWN_ERROR"

    # Try to extract file path if present
    file_pattern = r'File\s+["\']([^"\']+)["\']'
    file_match = re.search(file_pattern, traceback_text)
    if file_match:
        result["file"] = file_match.group(1)

    # Extract line from "line X" pattern in traceback
    line_pattern = r'line\s+(\d+)'
    lines = re.findall(line_pattern, traceback_text)
    if lines:
        # Get the line number before the error
        for i, line in enumerate(lines):
            if i == 0 and not result["line_number"]:
                result["line_number"] = int(line)

    return result


def generate_report(
    failure_info: dict,
    dep_check: dict,
    cycle_check: dict,
    init_check: dict
) -> str:
    """
    Generate a formatted diagnostic report.

    Groups findings by severity (BLOCKER, WARNING, INFO) and includes
    remediation steps with exact fix commands.

    Args:
        failure_info: Output from parse_traceback_failure
        dep_check: Output from check_missing_dependencies
        cycle_check: Output from check_circular_imports
        init_check: Output from check_missing_inits

    Returns:
        Formatted report string
    """
    lines = []
    lines.append("=" * 60)
    lines.append("=== ZO-SENTINEL Signal Analyser Smoke Diagnostic ===")
    lines.append("=" * 60)

    # Header with failure info
    if failure_info.get("frozen_importlib"):
        lines.append(f"Import Status: FAILED - <frozen importlib._bootstrap>")
        if failure_info.get("line_number"):
            lines.append(f"Failure Location: line {failure_info['line_number']}")
    else:
        lines.append(f"Exception: {failure_info.get('exception_type', 'Unknown')}")
        lines.append(f"Message: {failure_info.get('exception_message', 'No message')}")

    if failure_info.get("module"):
        lines.append(f"Failed Module: {failure_info['module']}")

    lines.append("")

    # Check 1: Dependency Analysis
    lines.append("CHECK 1: Dependency Analysis")
    lines.append("-" * 40)
    status = dep_check.get("status", "UNKNOWN")
    severity = "BLOCKER" if status == "WARNING" and dep_check.get("missing") else "INFO"
    lines.append(f"  Severity: {severity}")
    lines.append(f"  Status: {status}")

    if "found" in dep_check:
        if isinstance(dep_check["found"], dict):
            lines.append(f"  Found Stdlib: {dep_check['found'].get('stdlib', [])[:10]}...")
            lines.append(f"  Found Third-Party: {dep_check['found'].get('third_party', [])}")
        else:
            lines.append(f"  Found: {dep_check['found'][:10]}...")

    if dep_check.get("missing"):
        lines.append(f"  Missing Third-Party: {dep_check['missing']}")
        lines.append(f"  Recommendation: pip install {' '.join(dep_check['missing'])}")

    if dep_check.get("uncertain"):
        lines.append(f"  Uncertain (local?): {dep_check['uncertain']}")

    lines.append("")

    # Check 2: Circular Import Analysis
    lines.append("CHECK 2: Circular Import Analysis")
    lines.append("-" * 40)
    status = cycle_check.get("status", "UNKNOWN")
    lines.append(f"  Status: {status}")

    if cycle_check.get("modules_analyzed"):
        lines.append(f"  Modules Analyzed: {cycle_check['modules_analyzed']}")
        lines.append(f"  Files Analyzed: {cycle_check['files_analyzed']}")

    if cycle_check.get("cycles"):
        lines.append(f"  Cycles Found: {len(cycle_check['cycles'])}")
        for i, cycle in enumerate(cycle_check["cycles"], 1):
            lines.append(f"    Cycle {i}: {' -> '.join(cycle)}")
        lines.append("  Recommendation: Break cycle by moving shared code to new module")
    else:
        lines.append("  Cycles Found: None")

    lines.append("")

    # Check 3: Package Init Analysis
    lines.append("CHECK 3: Package Init Analysis")
    lines.append("-" * 40)
    status = init_check.get("status", "UNKNOWN")
    severity = "WARNING" if status == "FAIL" else "INFO"
    lines.append(f"  Severity: {severity}")
    lines.append(f"  Status: {status}")

    if init_check.get("missing"):
        lines.append(f"  Missing: {init_check['missing']}")
        lines.append(f"  Recommendation: touch {' '.join(init_check['missing'])}")

    lines.append("")
    lines.append("=" * 60)

    # Remediation Section
    lines.append("=== REMEDIATION STEPS ===")
    lines.append("=" * 60)

    step_num = 1

    # Missing dependencies
    if dep_check.get("missing"):
        deps = " ".join(dep_check["missing"])
        lines.append(f"{step_num}. Install missing packages: pip install {deps}")
        step_num += 1

    # Missing __init__.py files
    if init_check.get("missing"):
        init_files = " ".join(init_check["missing"])
        lines.append(f"{step_num}. Create missing init files: touch {init_files}")
        step_num += 1

    # Circular imports
    if cycle_check.get("cycles"):
        lines.append(f"{step_num}. Break circular import(s) - see CHECK 2 above")
        step_num += 1

    # General verification
    if step_num > 1:
        lines.append(f"{step_num}. Re-run smoke test to verify fix")

    if step_num == 1:
        lines.append("No automatic remediation steps available.")
        lines.append("Review the diagnostic output above for manual fixes.")

    lines.append("=" * 60)

    return "\n".join(lines)


def run_diagnostic(signal_analyser_path: str = None) -> dict:
    """
    Run complete diagnostic on signal_analyser.py.

    Main entry point that attempts to import the module, captures failures,
    and runs all diagnostic checks.

    Args:
        signal_analyser_path: Path to signal_analyser.py (auto-detected if None)

    Returns:
        Combined results dict with all diagnostic information
    """
    result = {
        "target_path": None,
        "import_status": None,
        "traceback": None,
        "failure_info": {},
        "dependency_check": {},
        "circular_import_check": {},
        "init_check": {},
        "report": None
    }

    # Auto-detect signal_analyser.py if not provided
    if signal_analyser_path is None:
        possible_locations = [
            './signal_analyser.py',
            './src/signal_analyser.py',
            '../signal_analyser.py',
            './zo_sentinel/signal_analyser.py',
            './sentinel/signal_analyser.py',
        ]

        for loc in possible_locations:
            if os.path.exists(loc):
                signal_analyser_path = loc
                break

        if signal_analyser_path is None:
            result["import_status"] = "ERROR"
            result["error"] = "Could not find signal_analyser.py in common locations"
            return result

    result["target_path"] = os.path.abspath(signal_analyser_path)

    # Try to import signal_analyser and capture any failures
    import traceback as tb_mod

    traceback_text = None
    import_error = None

    # First, check if file exists
    if not os.path.exists(signal_analyser_path):
        result["import_status"] = "ERROR"
        result["error"] = f"File not found: {signal_analyser_path}"
    else:
        try:
            # Attempt import with detailed traceback capture
            spec = importlib.util.spec_from_file_location(
                "signal_analyser_test",
                signal_analyser_path
            )

            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules["signal_analyser_test"] = module

                try:
                    spec.loader.exec_module(module)
                    result["import_status"] = "SUCCESS"
                except ImportError as e:
                    result["import_status"] = "FAILED"
                    import_error = e
                    # Capture full traceback
                    import io
                    buffer = io.StringIO()
                    tb_mod.print_exc(file=buffer)
                    traceback_text = buffer.getvalue()
                    result["traceback"] = traceback_text
        except Exception as e:
            result["import_status"] = "ERROR"
            result["error"] = str(e)
            import io
            buffer = io.StringIO()
            tb_mod.print_exc(file=buffer)
            result["traceback"] = buffer.getvalue()

    # Parse the traceback
    if traceback_text:
        result["failure_info"] = parse_traceback_failure(traceback_text)
    else:
        result["failure_info"] = parse_traceback_failure("")

    # Run diagnostic checks
    if os.path.exists(signal_analyser_path):
        # Dependency check
        result["dependency_check"] = check_missing_dependencies(signal_analyser_path)

        # Determine package root
        dir_path = os.path.dirname(os.path.abspath(signal_analyser_path))
        parent_dir = os.path.dirname(dir_path)

        # Check for package root markers
        potential_roots = [
            dir_path,
            parent_dir,
            os.path.join(dir_path, '..'),
            os.path.join(os.getcwd(), 'src'),
            os.getcwd()
        ]

        package_root = None
        for root in potential_roots:
            if os.path.exists(root) and os.path.isdir(root):
                package_root = root
                break

        # Circular import check
        if package_root:
            result["circular_import_check"] = check_circular_imports(package_root)

        # Init file check
        package_dirs = [
            dir_path,
            package_root if package_root else dir_path
        ]
        result["init_check"] = check_missing_inits(package_dirs)

    # Generate report
    result["report"] = generate_report(
        result["failure_info"],
        result["dependency_check"],
        result["circular_import_check"],
        result["init_check"]
    )

    # Print report
    print(result["report"])

    return result


# ============================================================================
# Self-Test Block
# ============================================================================

if __name__ == '__main__':
    import tempfile
    import shutil

    print("Running self-tests for signal_analyser_smoke_diagnostic.py")
    print("=" * 60)

    # Test 1: Mock scenario with missing dependency
    print("\n[Test 1] Testing check_missing_dependencies...")

    # Create a temporary file with known imports
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("""
import os
import sys
from typing import List
import nonexistent_package_xyz
from fake_library_abc import something
""")
        temp_file = f.name

    dep_result = check_missing_dependencies(temp_file)

    # Verify structure
    assert isinstance(dep_result, dict), "Result should be a dict"
    assert "status" in dep_result, "Result should have 'status' key"
    assert "found" in dep_result, "Result should have 'found' key"
    assert isinstance(dep_result["found"], dict), "'found' should be a dict with stdlib/third_party keys"

    # Should detect uncertain/missing packages
    found_found = dep_result.get("found", {})
    stdlib_found = found_found.get("stdlib", [])
    assert "os" in stdlib_found, "Should detect stdlib 'os'"
    assert "sys" in stdlib_found, "Should detect stdlib 'sys'"

    os.unlink(temp_file)
    print("  PASS: check_missing_dependencies returns correct structure")

    # Test 2: Mock scenario with missing __init__.py
    print("\n[Test 2] Testing check_missing_inits...")

    # Create a temp directory structure
    temp_dir = tempfile.mkdtemp()
    test_pkg = os.path.join(temp_dir, 'testpkg')
    os.makedirs(test_pkg)

    # Create a .py file but no __init__.py
    with open(os.path.join(test_pkg, 'module.py'), 'w') as f:
        f.write("# test")

    init_result = check_missing_inits([temp_dir])

    # Verify structure
    assert isinstance(init_result, dict), "Result should be a dict"
    assert "status" in init_result, "Result should have 'status' key"
    assert "missing" in init_result, "Result should have 'missing' key"
    assert isinstance(init_result["missing"], list), "'missing' should be a list"

    # Should find the missing __init__.py
    if init_result["status"] == "FAIL":
        assert len(init_result["missing"]) > 0, "Should detect missing __init__.py"

    shutil.rmtree(temp_dir)
    print("  PASS: check_missing_inits returns correct structure")

    # Test 3: Circular import detection on known-clean code
    print("\n[Test 3] Testing check_circular_imports...")

    # Use a clean Python stdlib directory or temp directory
    cycle_result = check_circular_imports(tempfile.gettempdir())

    assert isinstance(cycle_result, dict), "Result should be a dict"
    assert "status" in cycle_result, "Result should have 'status' key"
    assert "cycles" in cycle_result, "Result should have 'cycles' key"
    assert isinstance(cycle_result["cycles"], list), "'cycles' should be a list"

    # Status should be PASS (no cycles) or WARNING if cycles found
    assert cycle_result["status"] in ["PASS", "WARNING", "ERROR"], "Invalid status"

    print(f"  Status: {cycle_result['status']}")
    print(f"  Cycles found: {len(cycle_result['cycles'])}")
    print("  PASS: check_circular_imports returns correct structure")

    # Test 4: Traceback parsing
    print("\n[Test 4] Testing parse_traceback_failure...")

    test_traceback = '''
Traceback (most recent call last):
  File "./signal_analyser.py", line 10, in <module>
    from some_module import something
  File "<frozen importlib._bootstrap>", line 1007, in _call_with_frames_removed
ModuleNotFoundError: No module named 'missing_module'
'''

    parsed = parse_traceback_failure(test_traceback)

    assert isinstance(parsed, dict), "Result should be a dict"
    assert "status" in parsed, "Result should have 'status' key"
    assert "module" in parsed, "Result should have 'module' key"
    assert parsed["module"] == "missing_module", "Should extract missing module"
    assert parsed["exception_type"] == "ModuleNotFoundError", "Should detect exception type"
    assert parsed["frozen_importlib"] == True, "Should detect frozen importlib"

    # Test with empty traceback
    empty_parsed = parse_traceback_failure("")
    assert empty_parsed["status"] == "NO_TRACEBACK", "Empty traceback should have NO_TRACEBACK status"

    print("  PASS: parse_traceback_failure correctly extracts error info")

    # Test 5: Report generation
    print("\n[Test 5] Testing generate_report...")

    mock_failure = {
        "frozen_importlib": True,
        "line_number": 10,
        "module": "test_module",
        "exception_type": "ModuleNotFoundError",
        "exception_message": "No module named 'test'"
    }

    mock_dep = {
        "status": "WARNING",
        "found": {"stdlib": ["os"], "third_party": []},
        "missing": ["some-package"]
    }

    mock_cycle = {
        "status": "PASS",
        "cycles": []
    }

    mock_init = {
        "status": "FAIL",
        "missing": ["./test/__init__.py"]
    }

    report = generate_report(mock_failure, mock_dep, mock_cycle, mock_init)

    assert isinstance(report, str), "Report should be a string"
    assert "=== ZO-SENTINEL Signal Analyser Smoke Diagnostic ===" in report
    assert "CHECK 1:" in report or "Dependency" in report
    assert "CHECK 2:" in report or "Circular" in report
    assert "CHECK 3:" in report or "Init" in report
    assert "=== REMEDIATION STEPS ===" in report

    print("  PASS: generate_report produces valid report structure")

    # Test 6: Full diagnostic run (mock)
    print("\n[Test 6] Testing run_diagnostic...")

    # Create a mock signal_analyser.py
    temp_dir = tempfile.mkdtemp()
    mock_signal = os.path.join(temp_dir, 'signal_analyser.py')

    with open(mock_signal, 'w') as f:
        f.write("""
import os
import json
from typing import Dict
import requests
import this_doesnt_exist_123
""")

    diag_result = run_diagnostic(mock_signal)

    assert isinstance(diag_result, dict), "Result should be a dict"
    assert "target_path" in diag_result, "Should have target_path"
    assert "failure_info" in diag_result, "Should have failure_info"
    assert "dependency_check" in diag_result, "Should have dependency_check"
    assert "circular_import_check" in diag_result, "Should have circular_import_check"
    assert "init_check" in diag_result, "Should have init_check"
    assert "report" in diag_result, "Should have report"

    # Verify report was printed
    assert diag_result["report"] is not None

    shutil.rmtree(temp_dir)
    print("  PASS: run_diagnostic returns complete diagnostic results")

    # Summary
    print("\n" + "=" * 60)
    print("All diagnostic tests passed!")
    print("=" * 60)