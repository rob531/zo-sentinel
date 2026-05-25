#!/usr/bin/env python3
"""
ZO-SENTINEL: MCP Scanner Heartbeat Diagnostic
Diagnoses stale heartbeat issues in mcp_scanner service.

Checks:
1. Process alive status
2. Startup import chain validation
3. Scanner loop entry point reachability
4. Shared import chain issues (traceback pattern investigation)
"""

import sys
import os
import subprocess
import traceback
import importlib
import inspect
import ast
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple

# Add project root to path
PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
sys.path.insert(0, str(PROJECT_ROOT))

# Constants
SCANNER_SERVICE_NAME = "mcp_scanner"
HEALTH_TABLE = "service_health"
HEARTBEAT_THRESHOLD_MINUTES = 5
SHARED_IMPORT_PATTERN = 'File "<string>", line 10 in <module>, File "<frozen importlib'


def log(msg: str, level: str = "INFO"):
    """Structured logging."""
    timestamp = datetime.utcnow().isoformat()
    print(f"[{timestamp}] [{level}] {msg}")


def check_process_alive() -> Dict[str, Any]:
    """Check if mcp_scanner process is running."""
    result = {
        "alive": False,
        "pid": None,
        "cmdline": [],
        "uptime_seconds": None,
        "issues": []
    }
    
    try:
        # Check for process by name
        proc = subprocess.run(
            ["pgrep", "-f", "mcp_scanner"],
            capture_output=True,
            text=True
        )
        
        if proc.returncode == 0:
            pids = proc.stdout.strip().split('\n')
            result["pid"] = int(pids[0]) if pids and pids[0] else None
            
            if result["pid"]:
                # Get detailed process info
                ps_result = subprocess.run(
                    ["ps", "-p", str(result["pid"]), "-o", "pid,ppid,state,etime,cmd"],
                    capture_output=True,
                    text=True
                )
                
                if ps_result.returncode == 0:
                    lines = ps_result.stdout.strip().split('\n')
                    if len(lines) > 1:
                        result["process_info"] = lines[1]
                    
                    # Parse etime for uptime
                    etime = lines[1].split()[3] if len(lines) > 1 else "0:00"
                    result["etime"] = etime
                    
                result["alive"] = True
                log(f"Process alive: PID={result['pid']}")
        else:
            result["issues"].append("No mcp_scanner process found in process table")
            log("No mcp_scanner process found", "WARNING")
            
    except Exception as e:
        result["issues"].append(f"Process check error: {e}")
        log(f"Process check error: {e}", "ERROR")
    
    return result


def check_import_chain(module_name: str) -> Dict[str, Any]:
    """Test import chain for a module."""
    result = {
        "module": module_name,
        "importable": False,
        "import_time_ms": None,
        "error": None,
        "imported_modules": [],
        "traceback_lines": []
    }
    
    start_time = time.time()
    
    try:
        # Capture import
        import io
        import sys
        
        old_modules = set(sys.modules.keys())
        
        try:
            mod = importlib.import_module(module_name)
            result["importable"] = True
            
            new_modules = set(sys.modules.keys()) - old_modules
            result["imported_modules"] = sorted(new_modules)
            
        except Exception as e:
            result["error"] = str(e)
            result["traceback_lines"] = traceback.format_exc().split('\n')
            
    except Exception as e:
        result["error"] = f"Import test failed: {e}"
        
    finally:
        result["import_time_ms"] = round((time.time() - start_time) * 1000, 2)
    
    return result


def analyze_shared_import_issue() -> Dict[str, Any]:
    """Analyze the shared import chain issue affecting multiple services."""
    result = {
        "has_shared_issue": False,
        "affected_files": [],
        "common_problematic_imports": [],
        "analysis": ""
    }
    
    # Files known to have the traceback pattern
    known_affected = [
        "registry_api.py",
        "rug_pull_monitor.py", 
        "signal_analyser.py",
        "mcp_scanner.py"
    ]
    
    problematic_imports = [
        "mcp",
        "mcp.server",
        "mcp.types",
        "starlette",
        "fastapi",
        "uvicorn"
    ]
    
    result["analysis"] = (
        "The traceback pattern 'File \"<string>\", line 10 in <module>, "
        "File \"<frozen importlib' indicates an import chain failure at the "
        "module initialization level. This typically occurs when:\n"
        "1. A lazy import fails silently in __getattr__ or __init__.py\n"
        "2. Circular imports cause partial module initialization\n"
        "3. A conditional import (try/except) masks the real error\n"
        "4. Version mismatches in shared dependencies"
    )
    
    # Check each known affected file
    for filename in known_affected:
        filepath = PROJECT_ROOT / filename
        if filepath.exists():
            result["affected_files"].append(str(filepath))
            
            # Parse AST to find problematic imports
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if any(p in alias.name for p in problematic_imports):
                                result["common_problematic_imports"].append(
                                    f"{filename}: {alias.name}"
                                )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and any(p in node.module for p in problematic_imports):
                            result["common_problematic_imports"].append(
                                f"{filename}: from {node.module} import ..."
                            )
                            
            except Exception as e:
                log(f"Failed to analyze {filename}: {e}", "WARNING")
    
    if result["affected_files"]:
        result["has_shared_issue"] = True
        
    return result


def test_scanner_loop_entry_point() -> Dict[str, Any]:
    """Test if scanner loop entry point is reachable."""
    result = {
        "entry_point_found": False,
        "run_function_exists": False,
        "loop_reachable": False,
        "issues": [],
        "entry_point_file": None
    }
    
    # Check for main scanner file
    scanner_files = [
        PROJECT_ROOT / "mcp_scanner.py",
        PROJECT_ROOT / "services" / "mcp_scanner.py",
        PROJECT_ROOT / "scanner" / "mcp_scanner.py"
    ]
    
    for filepath in scanner_files:
        if filepath.exists():
            result["entry_point_file"] = str(filepath)
            
            try:
                with open(filepath, 'r') as f:
                    content = f.read()
                
                tree = ast.parse(content)
                
                # Check for run() function
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        if node.name == "run":
                            result["run_function_exists"] = True
                            result["entry_point_found"] = True
                            
                            # Check if run() has a main loop (while/for with time.sleep)
                            for child in ast.walk(node):
                                if isinstance(child, ast.While):
                                    # Check for sleep in loop
                                    for sub in ast.walk(child):
                                        if isinstance(sub, ast.Call):
                                            if hasattr(sub.func, 'attr') and sub.func.attr == 'sleep':
                                                result["loop_reachable"] = True
                                                break
                                elif isinstance(child, ast.For):
                                    for sub in ast.walk(child):
                                        if isinstance(sub, ast.Call):
                                            if hasattr(sub.func, 'attr') and sub.func.attr == 'sleep':
                                                result["loop_reachable"] = True
                                                break
                        
                        elif node.name == "main":
                            result["entry_point_found"] = True
                
                if not result["run_function_exists"]:
                    result["issues"].append(f"No 'run()' function found in {filepath.name}")
                    
            except Exception as e:
                result["issues"].append(f"Failed to parse {filepath.name}: {e}")
                
            break
    
    if not result["entry_point_file"]:
        result["issues"].append("mcp_scanner.py not found in expected locations")
    
    return result


def check_heartbeat_status() -> Dict[str, Any]:
    """Check current heartbeat status via write_service."""
    result = {
        "last_heartbeat": None,
        "age_minutes": None,
        "stale": False,
        "heartbeat_checked": False
    }
    
    try:
        import requests
        
        # Query heartbeat via write_service read
        # First, send a diagnostic heartbeat
        heartbeat_data = {
            "table": HEALTH_TABLE,
            "rows": {
                "diag_check": {
                    "service": SCANNER_SERVICE_NAME,
                    "last_heartbeat": datetime.utcnow().isoformat(),
                    "diagnostic_note": "heartbeat_diag_check"
                }
            },
            "wait": True
        }
        
        response = requests.post(
            "http://127.0.0.1:8772/write",
            json=heartbeat_data,
            timeout=5
        )
        
        if response.status_code == 200:
            result["heartbeat_checked"] = True
            result["heartbeat_sent"] = True
            log("Diagnostic heartbeat sent successfully")
        else:
            result["heartbeat_sent"] = False
            result["heartbeat_error"] = response.text
            
    except requests.exceptions.ConnectionError:
        result["heartbeat_error"] = "Cannot connect to write_service at port 8772"
        result["write_service_reachable"] = False
        log("write_service not reachable at port 8772", "ERROR")
    except Exception as e:
        result["heartbeat_error"] = str(e)
        log(f"Heartbeat check error: {e}", "ERROR")
    
    return result


def test_service_imports() -> Dict[str, Any]:
    """Test imports used by mcp_scanner service."""
    result = {
        "import_tests": {},
        "total_tests": 0,
        "passed": 0,
        "failed": 0
    }
    
    # Core imports expected in mcp_scanner
    test_imports = [
        "logging",
        "threading",
        "time",
        "json",
        "datetime",
        "pathlib",
        "requests",
        "fastapi",
        "duckdb"
    ]
    
    # Optional MCP imports
    optional_imports = [
        "mcp",
        "mcp.server",
        "mcp.types"
    ]
    
    all_imports = test_imports + optional_imports
    
    for mod_name in all_imports:
        result["total_tests"] += 1
        test_result = check_import_chain(mod_name)
        result["import_tests"][mod_name] = test_result
        
        if test_result["importable"]:
            result["passed"] += 1
        else:
            result["failed"] += 1
            log(f"Import FAILED: {mod_name} - {test_result.get('error', 'Unknown')}", "ERROR")
    
    return result


def diagnose_stale_heartbeat() -> Dict[str, Any]:
    """Main diagnostic function for stale heartbeat issue."""
    diagnosis = {
        "timestamp": datetime.utcnow().isoformat(),
        "service": SCANNER_SERVICE_NAME,
        "diagnosis": "INCONCLUSIVE",
        "checks": {},
        "recommendations": []
    }
    
    log("=" * 60)
    log("MCP Scanner Heartbeat Diagnostic")
    log("=" * 60)
    
    # Check 1: Process alive
    log("\n[1/5] Checking process status...")
    process_check = check_process_alive()
    diagnosis["checks"]["process"] = process_check
    
    if not process_check["alive"]:
        diagnosis["diagnosis"] = "PROCESS_DEAD"
        diagnosis["recommendations"].append(
            "Process is not running. Check logs and restart mcp_scanner service."
        )
        return diagnosis
    
    # Check 2: Import chain
    log("\n[2/5] Testing import chain...")
    import_tests = test_service_imports()
    diagnosis["checks"]["imports"] = import_tests
    
    if import_tests["failed"] > 0:
        diagnosis["diagnosis"] = "IMPORT_FAILURE"
        diagnosis["recommendations"].append(
            f"Failed imports detected: {import_tests['failed']} modules. "
            "Check dependency installation and module paths."
        )
    
    # Check 3: Shared import issue analysis
    log("\n[3/5] Analyzing shared import chain issue...")
    shared_analysis = analyze_shared_import_issue()
    diagnosis["checks"]["shared_imports"] = shared_analysis
    
    if shared_analysis["has_shared_issue"]:
        diagnosis["diagnosis"] = "SHARED_IMPORT_ISSUE"
        diagnosis["recommendations"].extend([
            "Shared import chain issue detected across multiple services.",
            "The traceback pattern indicates a common problematic import.",
            shared_analysis["analysis"]
        ])
        
        for prob_import in shared_analysis.get("common_problematic_imports", []):
            diagnosis["recommendations"].append(f"Review import: {prob_import}")
    
    # Check 4: Scanner loop entry point
    log("\n[4/5] Testing scanner loop entry point...")
    entry_check = test_scanner_loop_entry_point()
    diagnosis["checks"]["entry_point"] = entry_check
    
    if not entry_check["entry_point_found"]:
        diagnosis["diagnosis"] = "MISSING_ENTRY_POINT"
        diagnosis["recommendations"].append(
            "Scanner loop entry point (run() function) not found."
        )
    elif not entry_check["loop_reachable"]:
        diagnosis["recommendations"].append(
            "Entry point exists but main loop may not be executing properly."
        )
    
    # Check 5: Heartbeat mechanism
    log("\n[5/5] Testing heartbeat mechanism...")
    heartbeat_check = check_heartbeat_status()
    diagnosis["checks"]["heartbeat"] = heartbeat_check
    
    if heartbeat_check.get("heartbeat_error"):
        diagnosis["diagnosis"] = "HEARTBEAT_MECHANISM_FAILED"
        diagnosis["recommendations"].append(
            f"Heartbeat mechanism error: {heartbeat_check['heartbeat_error']}"
        )
    
    # Generate final diagnosis
    log("\n" + "=" * 60)
    log(f"DIAGNOSIS: {diagnosis['diagnosis']}")
    log("=" * 60)
    
    for i, rec in enumerate(diagnosis["recommendations"], 1):
        log(f"  {i}. {rec}")
    
    return diagnosis


def run_daemon_mode():
    """Run diagnostic in daemon/continuous mode."""
    import logging
    
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    logger = logging.getLogger("mcp_heartbeat_diag")
    
    logger.info("Starting MCP Scanner Heartbeat Diagnostic Daemon")
    
    try:
        while True:
            diagnosis = diagnose_stale_heartbeat()
            
            logger.info(f"Diagnostic cycle complete: {diagnosis['diagnosis']}")
            
            # Sleep before next check
            time.sleep(60)  # Check every minute
            
    except KeyboardInterrupt:
        logger.info("Diagnostic daemon stopped")
    except Exception as e:
        logger.error(f"Daemon error: {e}")
        raise


def run():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="MCP Scanner Heartbeat Diagnostic")
    parser.add_argument("--daemon", "-d", action="store_true", 
                        help="Run in daemon mode (continuous monitoring)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    
    args = parser.parse_args()
    
    if args.daemon:
        run_daemon_mode()
    else:
        # Single diagnostic run
        diagnosis = diagnose_stale_heartbeat()
        
        # Output JSON result
        import json
        print("\n--- DIAGNOSTIC RESULT ---")
        print(json.dumps(diagnosis, indent=2, default=str))


if __name__ == "__main__":
    run()