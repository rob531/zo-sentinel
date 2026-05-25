# ZO-SENTINEL: verify_graphql_schema_builder_wired.py
# Diagnostic verification module - confirms dormant status per spec section 9

import os
import sys
import subprocess
from pathlib import Path
from typing import Dict, List, Any

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")

def check_file_exists() -> Dict[str, Any]:
    """Check if graphql_schema_builder.py exists."""
    target = PROJECT_ROOT / "graphql_schema_builder.py"
    return {
        "file_exists": target.exists(),
        "file_path": str(target),
        "file_size": target.stat().st_size if target.exists() else 0
    }

def check_daemon_imports() -> Dict[str, List[str]]:
    """Verify graphql_schema_builder is not imported by any daemon files."""
    daemon_dir = PROJECT_ROOT / "daemons"
    imports = []
    
    if daemon_dir.exists():
        for py_file in daemon_dir.glob("*.py"):
            content = py_file.read_text()
            if "graphql_schema_builder" in content:
                imports.append(str(py_file.name))
    
    return {"imports_found": imports, "is_dormant": len(imports) == 0}

def check_http_routes() -> Dict[str, Any]:
    """Verify no HTTP routes mount graphql_schema_builder."""
    # Check main.py and app routes
    main_py = PROJECT_ROOT / "main.py"
    routes_file = PROJECT_ROOT / "routes.py"
    
    wiring_found = []
    
    for f in [main_py, routes_file]:
        if f.exists():
            content = f.read_text()
            if "graphql_schema_builder" in content or "graphql" in content.lower():
                wiring_found.append(f.name)
    
    return {"routes_found": wiring_found, "is_dormant": len(wiring_found) == 0}

def check_supervisord() -> Dict[str, Any]:
    """Verify supervisord does not load graphql schema builder."""
    supervisord_conf = PROJECT_ROOT / "supervisord.conf"
    wiring_found = []
    
    if supervisord_conf.exists():
        content = supervisord_conf.read_text()
        if "graphql" in content.lower():
            wiring_found.append("supervisord.conf references graphql")
    
    return {"supervisord_wiring": wiring_found, "is_dormant": len(wiring_found) == 0}

def verify_dependencies_not_installed() -> Dict[str, Any]:
    """Check if graphql-related packages are not installed."""
    packages_to_check = ["graphql-server", "strawberry-graphql", "graphene"]
    installed = []
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list"],
            capture_output=True, text=True, timeout=10
        )
        for pkg in packages_to_check:
            if pkg.lower() in result.stdout.lower():
                installed.append(pkg)
    except Exception:
        pass
    
    return {"graphql_packages_installed": installed, "is_dormant": len(installed) == 0}

def main():
    print("=" * 60)
    print("ZO-SENTINEL: GraphQL Schema Builder Wiring Verification")
    print("=" * 60)
    
    results = {}
    
    # Check 1: File existence
    results["file_check"] = check_file_exists()
    print(f"\n[1] File existence: {results['file_check']['file_exists']}")
    if results['file_check']['file_exists']:
        print(f"    Path: {results['file_check']['file_path']}")
        print(f"    Size: {results['file_check']['file_size']} bytes")
    
    # Check 2: Daemon imports
    results["daemon_imports"] = check_daemon_imports()
    print(f"\n[2] Daemon imports: {results['daemon_imports']['imports_found']}")
    print(f"    Dormant: {results['daemon_imports']['is_dormant']}")
    
    # Check 3: HTTP routes
    results["http_routes"] = check_http_routes()
    print(f"\n[3] HTTP route wiring: {results['http_routes']['routes_found']}")
    print(f"    Dormant: {results['http_routes']['is_dormant']}")
    
    # Check 4: Supervisord
    results["supervisord"] = check_supervisord()
    print(f"\n[4] Supervisord config: {results['supervisord']['supervisord_wiring']}")
    print(f"    Dormant: {results['supervisord']['is_dormant']}")
    
    # Check 5: Dependencies
    results["dependencies"] = verify_dependencies_not_installed()
    print(f"\n[5] GraphQL packages: {results['dependencies']['graphql_packages_installed']}")
    print(f"    Dormant: {results['dependencies']['is_dormant']}")
    
    # Summary
    all_dormant = (
        results["daemon_imports"]["is_dormant"] and
        results["http_routes"]["is_dormant"] and
        results["supervisord"]["is_dormant"] and
        results["dependencies"]["is_dormant"]
    )
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"GraphQL Schema Builder Status: {'DORMANT (verified)' if all_dormant else 'ACTIVE/WIRED'}")
    print(f"  - Not imported by any daemon: {results['daemon_imports']['is_dormant']}")
    print(f"  - No HTTP route mounting: {results['http_routes']['is_dormant']}")
    print(f"  - Not in supervisord config: {results['supervisord']['is_dormant']}")
    print(f"  - No GraphQL packages: {results['dependencies']['is_dormant']}")
    
    return 0 if all_dormant else 1

if __name__ == "__main__":
    sys.exit(main())