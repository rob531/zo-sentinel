#!/usr/bin/env python3
"""
smoke_import_diagnostics_v2.py
Diagnostic module for smoke test failures at line 10 import traceback.
Inspects PYTHONPATH, sys.modules, and importlib mechanics.
Writes findings to service_health.meta as JSON diagnostic blob.
DO NOT attempt to fix imports - only diagnose.
"""

import sys
import os
import json
import traceback
import importlib
import importlib.util
from datetime import datetime
from pathlib import Path

SERVICE_NAME = "smoke_import_diagnostics"
META_FILE = "/tmp/service_health.meta"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

# Files showing identical line 10 import failure
TARGET_FILES = [
    "/home/workspace/registry_api.py",
    "/home/workspace/rug_pull_monitor.py",
    "/home/workspace/signal_analyser.py",
]


def check_single_instance():
    """Ensure single instance via PID file."""
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing_pid = int(f.read().strip())
        try:
            os.kill(existing_pid, 0)
            print(f"[FATAL] Service already running as PID {existing_pid}")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def send_heartbeat():
    """Update service_health meta file."""
    meta = {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat(),
        "status": "running",
    }
    try:
        with open(META_FILE, "w") as f:
            json.dump(meta, f)
    except Exception as e:
        print(f"[WARN] Could not write heartbeat: {e}")


def write_diagnostics(diagnostics: dict):
    """Write diagnostic blob to meta file."""
    diagnostics["written_at"] = datetime.utcnow().isoformat()
    try:
        with open(META_FILE, "w") as f:
            json.dump(diagnostics, f, indent=2, default=str)
        print(f"[OK] Diagnostics written to {META_FILE}")
    except Exception as e:
        print(f"[ERROR] Failed to write diagnostics: {e}")


def inspect_line_10_imports(filepath: str) -> dict:
    """Read file and identify what's on line 10."""
    result = {
        "file": filepath,
        "exists": os.path.exists(filepath),
        "line_10": None,
        "line_10_imports": [],
        "all_imports": [],
        "read_error": None,
    }
    
    if not result["exists"]:
        return result
    
    try:
        with open(filepath, "r") as f:
            lines = f.readlines()
        
        # Collect all import lines
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            result["all_imports"].append({"line": i, "content": stripped})
            if i == 10:
                result["line_10"] = stripped
        
        # Parse line 10 for import statements
        if result["line_10"]:
            line = result["line_10"]
            if line.startswith("import ") or line.startswith("from "):
                result["line_10_imports"].append(line)
    except Exception as e:
        result["read_error"] = str(e)
    
    return result


def test_import(import_statement: str) -> dict:
    """Attempt import and capture result."""
    result = {
        "statement": import_statement,
        "success": False,
        "error": None,
        "module": None,
    }
    
    try:
        # Extract module name
        if import_statement.startswith("from "):
            parts = import_statement.split()
            if len(parts) >= 2:
                result["module"] = parts[1].split(".")[0]
        elif import_statement.startswith("import "):
            result["module"] = import_statement.split()[1].split(".")[0]
        
        # Attempt import
        exec(import_statement, {})
        result["success"] = True
    except Exception as e:
        result["error"] = traceback.format_exception_only(type(e), e)[-1].strip()
    
    return result


def check_module_in_sys_modules(module_name: str) -> dict:
    """Check if module is in sys.modules."""
    in_modules = module_name in sys.modules
    spec_exists = importlib.util.find_spec(module_name) is not None
    
    return {
        "module": module_name,
        "in_sys_modules": in_modules,
        "spec_exists": spec_exists,
        "module_path": None,
    }


def run():
    """Main diagnostic routine."""
    check_single_instance()
    print(f"[START] {SERVICE_NAME} diagnostic run")
    
    diagnostics = {
        "service": SERVICE_NAME,
        "python_version": sys.version,
        "python_executable": sys.executable,
        "python_path": sys.path.copy(),
        "files_analyzed": [],
        "common_line_10_pattern": None,
        "import_attempts": [],
        "sys_modules_check": [],
        "recommendations": [],
    }
    
    # Analyze target files
    line_10_contents = {}
    all_imports_found = []
    
    for filepath in TARGET_FILES:
        print(f"[INFO] Analyzing {filepath}")
        file_analysis = inspect_line_10_imports(filepath)
        diagnostics["files_analyzed"].append(file_analysis)
        
        if file_analysis["line_10"]:
            line_10_contents[filepath] = file_analysis["line_10"]
        
        all_imports_found.extend(file_analysis["all_imports"])
    
    # Identify common line 10 pattern
    unique_line_10 = set(line_10_contents.values())
    if len(unique_line_10) == 1:
        common_line = list(unique_line_10)[0]
        diagnostics["common_line_10_pattern"] = {
            "content": common_line,
            "affects_all_files": True,
        }
        print(f"[FINDING] All files have identical line 10: {common_line}")
    
    # Test the common line 10 import
    if diagnostics["common_line_10_pattern"]:
        import_stmt = diagnostics["common_line_10_pattern"]["content"]
        import_result = test_import(import_stmt)
        diagnostics["import_attempts"].append(import_result)
        
        if import_result["module"]:
            module_check = check_module_in_sys_modules(import_result["module"])
            diagnostics["sys_modules_check"].append(module_check)
    
    # Analyze all import lines
    import_modules = set()
    for imp in all_imports_found:
        content = imp["content"]
        if content.startswith("from "):
            parts = content.split()
            if len(parts) >= 2:
                import_modules.add(parts[1].split(".")[0])
        elif content.startswith("import "):
            parts = content.split()
            if len(parts) >= 2:
                import_modules.add(parts[1].split(".")[0])
    
    diagnostics["all_imported_modules"] = list(import_modules)
    
    # Check for missing dependencies
    missing = []
    for mod in import_modules:
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
    
    if missing:
        diagnostics["recommendations"].append({
            "type": "missing_modules",
            "modules": missing,
            "suggestion": "Install missing modules or verify PYTHONPATH includes their location",
        })
    
    # Write diagnostics
    write_diagnostics(diagnostics)
    send_heartbeat()
    
    print(f"[DONE] Diagnostic run complete")
    print(f"[SUMMARY] Files: {len(TARGET_FILES)}, Common line 10: {diagnostics.get('common_line_10_pattern', {}).get('content', 'N/A')}")
    
    return diagnostics


if __name__ == "__main__":
    run()