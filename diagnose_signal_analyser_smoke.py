#!/usr/bin/env python3
"""
diagnose_signal_analyser_smoke.py
Diagnostic daemon to investigate recent smoke failure in signal_analyser.py
"""

import os
import sys
import ast
import time
import json
import subprocess
import traceback as tb_module
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Service configuration
SERVICE_NAME = "diagnose_signal_analyser_smoke"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
POLL_SECS = 30

# Paths
SIGNAL_ANALYSER_PATH = "/home/workspace/services/signal_analyser.py"
SMOKE_LOG_PATH = "/tmp/smoke_signal_analyser.log"
AUDIT_LOG_TABLE = "audit_log"

# Port 8772 is write_service
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"


def check_single_instance() -> bool:
    """Ensure only single instance runs."""
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            existing_pid = int(f.read().strip())
        try:
            os.kill(existing_pid, 0)
            print(f"[{SERVICE_NAME}] Another instance running with PID {existing_pid}")
            return False
        except OSError:
            print(f"[{SERVICE_NAME}] Stale PID file found, replacing")
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    return True


def send_heartbeat():
    """Send heartbeat to write_service."""
    try:
        import requests
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat()
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[{SERVICE_NAME}] Heartbeat failed: {e}")


def write_audit_log(event_type: str, detail: str, target_server_id: str = "diagnostic"):
    """Write entry to audit_log table."""
    try:
        import requests
        payload = {
            "table": AUDIT_LOG_TABLE,
            "rows": {
                "target_server_id": target_server_id,
                "event_type": event_type,
                "actor": SERVICE_NAME,
                "detail": detail,
                "created_at": datetime.utcnow().isoformat()
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[{SERVICE_NAME}] Audit log write failed: {e}")


def get_smoke_log_content() -> Optional[str]:
    """Read the smoke log content."""
    if not os.path.exists(SMOKE_LOG_PATH):
        return None
    try:
        with open(SMOKE_LOG_PATH, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"[{SERVICE_NAME}] Failed to read smoke log: {e}")
        return None


def parse_import_error(traceback_text: str) -> Dict:
    """Parse traceback to extract import-related error details."""
    result = {
        "error_type": None,
        "module_name": None,
        "error_message": None,
        "line_number": None,
        "full_traceback": traceback_text[:2000] if traceback_text else ""
    }
    
    if not traceback_text:
        return result
    
    lines = traceback_text.split('\n')
    
    # Look for ImportError, ModuleNotFoundError, AttributeError patterns
    for i, line in enumerate(lines):
        line = line.strip()
        if 'ImportError' in line or 'ModuleNotFoundError' in line:
            result["error_type"] = "ImportError"
            result["error_message"] = line
            # Try to extract module name
            if "named" in line:
                parts = line.split("named")
                if len(parts) > 1:
                    result["module_name"] = parts[1].strip().strip("'\"")
            elif "'" in line:
                start = line.find("'")
                end = line.rfind("'")
                if start != end:
                    result["module_name"] = line[start+1:end]
        elif 'AttributeError' in line:
            result["error_type"] = "AttributeError"
            result["error_message"] = line
    
    # Find the relevant source file and line
    for line in lines:
        if 'signal_analyser.py' in line or 'signal_analyser' in line:
            # Extract line number
            parts = line.split(',')
            for part in parts:
                if 'line' in part:
                    try:
                        num = ''.join(filter(str.isdigit, part))
                        if num:
                            result["line_number"] = int(num)
                    except:
                        pass
            break
    
    return result


def get_signal_analyser_imports() -> List[Dict]:
    """Extract import statements from signal_analyser.py."""
    imports = []
    
    if not os.path.exists(SIGNAL_ANALYSER_PATH):
        return [{"error": "signal_analyser.py not found", "module": "N/A"}]
    
    try:
        with open(SIGNAL_ANALYSER_PATH, 'r') as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "module": alias.name,
                        "alias": alias.asname,
                        "type": "import"
                    })
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    full_name = f"{module}.{alias.name}" if module else alias.name
                    imports.append({
                        "module": full_name,
                        "alias": alias.asname,
                        "type": "from_import",
                        "from_module": module
                    })
    except Exception as e:
        imports.append({"error": str(e), "module": "parse_error"})
    
    return imports


def check_module_availability(module_name: str) -> Dict:
    """Check if a module is available and get its version."""
    result = {
        "module": module_name,
        "available": False,
        "version": None,
        "path": None,
        "error": None
    }
    
    # Handle submodules
    base_module = module_name.split('.')[0]
    
    try:
        import importlib
        mod = importlib.import_module(module_name)
        result["available"] = True
        result["path"] = getattr(mod, '__file__', 'built-in')
        
        # Try to get version
        if hasattr(mod, '__version__'):
            result["version"] = mod.__version__
        else:
            # Try pkg_resources or importlib.metadata
            try:
                from importlib.metadata import version
                result["version"] = version(base_module)
            except:
                pass
                
    except ImportError as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = str(e)
    
    return result


def check_dependency_conflicts() -> List[Dict]:
    """Check for common dependency conflicts."""
    conflicts = []
    
    # Check for common problematic patterns
    common_issues = [
        {
            "pattern": "pydantic",
            "issue": "pydantic v1 vs v2 API differences"
        },
        {
            "pattern": "fastapi",
            "issue": "fastapi requires specific starlette version"
        },
        {
            "pattern": "numpy",
            "issue": "numpy C extension conflicts"
        }
    ]
    
    for issue in common_issues:
        try:
            import importlib
            importlib.import_module(issue["pattern"])
        except ImportError:
            pass  # Module not installed, not a conflict
    
    return conflicts


def run_diagnostic() -> Dict:
    """Run the full diagnostic analysis."""
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "signal_analyser_path": SIGNAL_ANALYSER_PATH,
        "smoke_log_exists": os.path.exists(SMOKE_LOG_PATH),
        "import_error": None,
        "signal_analyser_imports": [],
        "dependency_check_results": [],
        "recommendations": []
    }
    
    # Step 1: Get smoke log content and parse error
    smoke_content = get_smoke_log_content()
    if smoke_content:
        report["import_error"] = parse_import_error(smoke_content)
        print(f"[{SERVICE_NAME}] Parsed import error from smoke log")
        print(f"  Error type: {report['import_error']['error_type']}")
        print(f"  Module: {report['import_error']['module_name']}")
        print(f"  Message: {report['import_error']['error_message']}")
    else:
        print(f"[{SERVICE_NAME}] No smoke log found at {SMOKE_LOG_PATH}")
        report["import_error"] = {"error": "No smoke log found"}
    
    # Step 2: Extract imports from signal_analyser.py
    report["signal_analyser_imports"] = get_signal_analyser_imports()
    print(f"[{SERVICE_NAME}] Found {len(report['signal_analyser_imports'])} imports in signal_analyser.py")
    
    # Step 3: Check availability of each imported module
    for imp in report["signal_analyser_imports"]:
        if "error" in imp:
            continue
        result = check_module_availability(imp["module"])
        report["dependency_check_results"].append(result)
        
        status = "✓" if result["available"] else "✗"
        print(f"  {status} {imp['module']}: available={result['available']}, version={result['version']}")
        
        if not result["available"] and report["import_error"].get("module_name") == imp["module"]:
            report["recommendations"].append({
                "module": imp["module"],
                "action": "install",
                "command": f"pip install {imp['module'].split('.')[0]}"
            })
    
    # Step 4: Generate recommendations
    if report["import_error"].get("error_type") == "ModuleNotFoundError":
        missing_module = report["import_error"].get("module_name")
        if missing_module:
            report["recommendations"].append({
                "type": "missing_dependency",
                "module": missing_module,
                "fix": f"pip install {missing_module}",
                "priority": "high"
            })
    
    return report


def create_report_summary(report: Dict) -> str:
    """Create a human-readable summary of the diagnostic report."""
    lines = [
        "=" * 60,
        f"DIAGNOSTIC REPORT: signal_analyser.py smoke failure",
        f"Generated: {report['timestamp']}",
        "=" * 60,
        "",
        "SMOKE LOG STATUS:",
        f"  - Log exists: {report['smoke_log_exists']}",
        ""
    ]
    
    if report["import_error"]:
        err = report["import_error"]
        lines.extend([
            "IMPORT ERROR DETAILS:",
            f"  - Error type: {err.get('error_type', 'Unknown')}",
            f"  - Missing module: {err.get('module_name', 'Unknown')}",
            f"  - Error message: {err.get('error_message', 'No message')}",
            f"  - Line number: {err.get('line_number', 'Unknown')}",
            ""
        ])
    
    lines.extend([
        "SIGNAL ANALYSER IMPORTS:",
        f"  Total imports found: {len(report['signal_analyser_imports'])}",
        ""
    ])
    
    unavailable = [r for r in report["dependency_check_results"] if not r["available"]]
    if unavailable:
        lines.append("  UNAVAILABLE MODULES:")
        for mod in unavailable:
            lines.append(f"    - {mod['module']}: {mod.get('error', 'Unknown error')}")
        lines.append("")
    
    if report["recommendations"]:
        lines.extend([
            "RECOMMENDATIONS:",
        ])
        for rec in report["recommendations"]:
            if rec.get("type") == "missing_dependency":
                lines.append(f"  [!] Install missing dependency: {rec['module']}")
                lines.append(f"      Command: {rec['fix']}")
                lines.append(f"      Priority: {rec.get('priority', 'medium')}")
            elif rec.get("action") == "install":
                lines.append(f"  [*] Install module: {rec['module']}")
                lines.append(f"      Command: {rec['command']}")
        lines.append("")
    
    lines.append("=" * 60)
    return "\n".join(lines)


def run():
    """Main daemon loop."""
    print(f"[{SERVICE_NAME}] Starting diagnostic daemon...")
    
    if not check_single_instance():
        sys.exit(1)
    
    print(f"[{SERVICE_NAME}] Running diagnostic analysis...")
    
    # Run the diagnostic
    report = run_diagnostic()
    
    # Generate summary
    summary = create_report_summary(report)
    print("\n" + summary)
    
    # Write to audit log
    detail = json.dumps({
        "smoke_log_found": report["smoke_log_exists"],
        "import_error": report["import_error"],
        "recommendations": report["recommendations"],
        "report": summary
    })
    write_audit_log("diagnostic_completed", detail, "signal_analyser")
    
    # Clean up PID file
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    
    print(f"[{SERVICE_NAME}] Diagnostic complete.")


if __name__ == '__main__':
    run()