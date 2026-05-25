import sys
import os
import importlib
import traceback
from pathlib import Path


def get_line_10_imports():
    """Extract what line 10 of registry_api.py imports"""
    registry_api_path = Path(__file__).parent / "registry_api.py"
    if not registry_api_path.exists():
        return {"error": "registry_api.py not found"}
    
    with open(registry_api_path, 'r') as f:
        lines = f.readlines()
    
    if len(lines) < 10:
        return {"error": "registry_api.py has fewer than 10 lines"}
    
    line_10 = lines[9].strip()
    return {"line_10": line_10, "raw": lines[:15]}


def check_module_importable(module_name):
    """Check if a module can be imported"""
    try:
        importlib.import_module(module_name)
        return {"status": "OK", "module": module_name}
    except ImportError as e:
        return {"status": "FAIL", "module": module_name, "error": str(e)}
    except Exception as e:
        return {"status": "ERROR", "module": module_name, "error": str(e)}


def diagnose_registry_import():
    """Main diagnostic for registry_api.py import smoke test"""
    results = {
        "test": "diagnose_registry_api_smoke_import",
        "target": "registry_api.py",
        "failure_point": "line_10",
        "import_checks": [],
        "dependency_status": {},
        "import_attempt": None,
        "import_error": None,
        "traceback_summary": None
    }
    
    # Get line 10 content
    line_info = get_line_10_imports()
    results["line_10_analysis"] = line_info
    
    # Extract module names from line 10 for checking
    line_10 = line_info.get("line_10", "")
    
    # Common patterns at line 10 for imports
    import_patterns = [
        "from", "import", "require", "__import__"
    ]
    
    # Try direct import of registry_api
    print("Attempting import of registry_api...")
    try:
        import registry_api
        results["import_attempt"] = {"status": "SUCCESS"}
        print("SUCCESS: registry_api imported successfully")
    except ImportError as e:
        results["import_attempt"] = {"status": "FAIL", "error": str(e)}
        results["import_error"] = str(e)
        results["traceback_summary"] = traceback.format_exc()
        print(f"FAIL: Import error - {e}")
        
        # Parse the error to identify specific failing module
        error_str = str(e)
        if "No module named" in error_str:
            missing_module = error_str.split("No module named")[-1].strip().strip("'\"")
            results["dependency_status"]["missing_module"] = missing_module
            check_result = check_module_importable(missing_module)
            results["dependency_status"][missing_module] = check_result
            print(f"MISSING MODULE: {missing_module}")
        elif "cannot import name" in error_str:
            parts = error_str.split("cannot import name")
            if len(parts) > 1:
                missing_name = parts[-1].split("from")[0].strip().strip("'\"")
                results["dependency_status"]["import_name"] = missing_name
                print(f"CIRCULAR/UNAVAILABLE IMPORT: {missing_name}")
    
    # Check known common dependencies for registry_api
    common_deps = [
        "fastapi", "uvicorn", "requests", "duckdb", "pydantic"
    ]
    
    print("\nChecking common dependencies...")
    for dep in common_deps:
        result = check_module_importable(dep)
        results["dependency_checks"][dep] = result
        status = result["status"]
        print(f"  {dep}: {status}")
    
    # Verify project structure
    project_root = Path(__file__).parent
    print(f"\nProject root: {project_root}")
    
    # Check for sibling modules that registry_api might depend on
    sibling_modules = [
        "threat_intel_ingestor",
        "signal_analyser", 
        "rug_pull_monitor"
    ]
    
    print("\nChecking sibling module availability...")
    for sibling in sibling_modules:
        sibling_path = project_root / f"{sibling}.py"
        if sibling_path.exists():
            try:
                importlib.import_module(sibling)
                results["dependency_status"][f"sibling:{sibling}"] = {"status": "OK", "found": True}
                print(f"  {sibling}: OK (exists and importable)")
            except ImportError as e:
                results["dependency_status"][f"sibling:{sibling}"] = {"status": "FAIL", "error": str(e)}
                print(f"  {sibling}: FAIL ({e})")
        else:
            results["dependency_status"][f"sibling:{sibling}"] = {"status": "NOT_FOUND", "path": str(sibling_path)}
            print(f"  {sibling}: NOT FOUND at {sibling_path}")
    
    return results


def main():
    print("=" * 60)
    print("ZO-SENTINEL: Registry API Import Diagnostic")
    print("=" * 60)
    print()
    
    results = diagnose_registry_import()
    
    print()
    print("=" * 60)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 60)
    
    if results["import_attempt"] and results["import_attempt"]["status"] == "SUCCESS":
        print("RESULT: PASS - registry_api imported successfully")
        print("The smoke test failure was transient or environment-dependent")
    else:
        print("RESULT: FAIL - Import issue detected")
        if results["dependency_status"]:
            print("\nDependency issues found:")
            for key, value in results["dependency_status"].items():
                print(f"  - {key}: {value}")
        if results["traceback_summary"]:
            print("\nFull traceback:")
            print(results["traceback_summary"])
    
    return results


if __name__ == "__main__":
    main()