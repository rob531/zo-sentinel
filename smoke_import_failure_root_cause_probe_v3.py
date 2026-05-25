import os
import sys
import json
import time
import logging
import traceback
import importlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple

import requests

SERVICE_NAME = "smoke_import_failure_root_cause_probe_v3"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
PID_FILE = "/tmp/smoke_import_failure_root_cause_probe_v3.pid"
LOG_FILE = "/home/workspace/logs/smoke_import_failure_root_cause_probe_v3.log"
POLL_SECS = 300

PROJECT_ROOT = "/home/workspace/zo_sentinel"
PROTECTED_MODULES = ["registry_api.py", "rug_pull_monitor.py", "signal_analyser.py"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and os.path.exists(f"/proc/{old_pid}"):
                log.warning(f"Another instance is running with PID {old_pid}")
                return False
            else:
                os.remove(PID_FILE)
                log.info("Stale PID file removed")
        except (ValueError, FileNotFoundError):
            os.remove(PID_FILE)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.error(f"Failed to create PID file: {e}")
        return False


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_sys_path_entries() -> List[str]:
    return sys.path.copy()


def get_loaded_modules() -> Dict[str, str]:
    modules = {}
    for name, mod in sys.modules.items():
        if mod and hasattr(mod, "__file__") and mod.__file__:
            modules[name] = mod.__file__
    return modules


def extract_imports_from_source(filepath: str) -> List[str]:
    imports = []
    try:
        with open(filepath, "r") as f:
            content = f.read()
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("import ") and not stripped.startswith("import ") and "from" not in stripped:
                parts = stripped[7:].strip().split(",")
                for part in parts:
                    module_name = part.strip().split(".")[0]
                    if module_name:
                        imports.append(module_name)
            elif stripped.startswith("from "):
                parts = stripped[4:].strip().split(" import")
                if parts:
                    module_name = parts[0].strip().split(".")[0]
                    if module_name:
                        imports.append(module_name)
    except Exception as e:
        log.error(f"Failed to extract imports from {filepath}: {e}")
    return imports


def resolve_import_path(module_name: str) -> Optional[str]:
    try:
        spec = importlib.util.find_spec(module_name)
        if spec and spec.origin:
            return spec.origin
        return None
    except Exception:
        return None


def test_module_import(module_name: str) -> Tuple[bool, Optional[str]]:
    try:
        importlib.import_module(module_name)
        return True, None
    except Exception as e:
        tb = traceback.format_exc()
        return False, tb


def trace_import_chain(module_name: str, visited: Optional[Set[str]] = None) -> Dict[str, Any]:
    if visited is None:
        visited = set()
    
    if module_name in visited:
        return {"module": module_name, "status": "circular", "error": "Circular import detected"}
    visited.add(module_name)
    
    result = {"module": module_name, "status": "unknown", "imports": [], "error": None, "file": None}
    
    spec = importlib.util.find_spec(module_name)
    if spec and spec.origin:
        result["file"] = spec.origin
        imports = extract_imports_from_source(spec.origin)
        for imp in imports[:20]:
            if imp not in visited:
                result["imports"].append(trace_import_chain(imp, visited.copy()))
    
    success, error = test_module_import(module_name)
    result["status"] = "ok" if success else "failed"
    result["error"] = error
    
    return result


def check_pycache_integrity(filepath: str) -> Dict[str, Any]:
    result = {"filepath": filepath, "has_pycache": False, "pyc_files": [], "issues": []}
    pycache_dir = Path(filepath).parent / "__pycache__"
    
    if pycache_dir.exists():
        result["has_pycache"] = True
        pyc_files = list(pycache_dir.glob("*.pyc"))
        for pyc in pyc_files:
            if filepath.endswith(".py"):
                module_name = Path(filepath).stem
                pyc_name = f"{module_name}.cpython"
                if pyc.name.startswith(pyc_name):
                    result["pyc_files"].append(str(pyc))
    
    pycache_path = Path(filepath).with_suffix(".pyc")
    if pycache_path.exists():
        result["pyc_files"].append(str(pycache_path))
    
    return result


def query_smoke_failure_history() -> List[Dict[str, Any]]:
    sql = """
    SELECT service, last_heartbeat, meta
    FROM service_health
    WHERE service IN ('smoke_test', 'registry_api', 'rug_pull_monitor', 'signal_analyser')
    ORDER BY last_heartbeat DESC
    LIMIT 50
    """
    return ws_query(sql)


def query_recent_import_errors() -> List[Dict[str, Any]]:
    sql = """
    SELECT * FROM service_health
    WHERE meta LIKE '%import%' OR meta LIKE '%ImportError%' OR meta LIKE '%ModuleNotFoundError%'
    ORDER BY last_heartbeat DESC
    LIMIT 20
    """
    return ws_query(sql)


def check_python_version_consistency() -> Dict[str, Any]:
    result = {"current": sys.version, "executable": sys.executable}
    
    try:
        proc = subprocess.run(
            [sys.executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        result["version_output"] = proc.stdout + proc.stderr
    except Exception as e:
        result["version_check_error"] = str(e)
    
    return result


def diagnose_systemic_import_issues() -> Dict[str, Any]:
    result = {
        "sys_path": get_sys_path_entries(),
        "loaded_modules_count": len(get_loaded_modules()),
        "python_version": check_python_version_consistency(),
        "std_lib_check": {},
        "third_party_check": {},
        "project_check": {},
        "systemic": False,
        "systemic_reason": None
    }
    
    stdlib_modules = ["os", "sys", "json", "logging", "time", "datetime", "pathlib", "traceback", "importlib"]
    for mod in stdlib_modules:
        success, error = test_module_import(mod)
        result["std_lib_check"][mod] = {"ok": success, "error": str(error) if error else None}
        if not success:
            result["systemic"] = True
            result["systemic_reason"] = f"Standard library module '{mod}' failed to import"
    
    third_party_modules = ["requests", "fastapi", "uvicorn"]
    for mod in third_party_modules:
        success, error = test_module_import(mod)
        result["third_party_check"][mod] = {"ok": success, "error": str(error) if error else None}
    
    project_path = PROJECT_ROOT
    if os.path.exists(project_path):
        result["project_check"]["exists"] = True
        result["project_check"]["readable"] = os.access(project_path, os.R_OK)
        if project_path not in sys.path:
            result["systemic"] = True
            result["systemic_reason"] = f"Project path '{project_path}' not in sys.path"
    else:
        result["project_check"]["exists"] = False
    
    return result


def diagnose_protected_module(module_name: str) -> Dict[str, Any]:
    filepath = os.path.join(PROJECT_ROOT, module_name)
    result = {
        "module": module_name,
        "filepath": filepath,
        "exists": os.path.exists(filepath),
        "readable": os.access(filepath, os.R_OK) if os.path.exists(filepath) else False,
        "imports": [],
        "import_chain": {},
        "pycache_check": {},
        "import_status": "unknown",
        "error": None,
        "error_type": None,
        "traceback_lines": []
    }
    
    if not result["exists"]:
        result["import_status"] = "file_not_found"
        return result
    
    result["imports"] = extract_imports_from_source(filepath)
    result["pycache_check"] = check_pycache_integrity(filepath)
    
    try:
        success, error = test_module_import(module_name.replace(".py", ""))
        result["import_status"] = "ok" if success else "failed"
        if error:
            tb_lines = error.strip().split("\n")
            result["error"] = tb_lines[-1] if tb_lines else error
            result["traceback_lines"] = tb_lines
            if "ImportError" in error:
                result["error_type"] = "ImportError"
            elif "ModuleNotFoundError" in error:
                result["error_type"] = "ModuleNotFoundError"
            elif "AttributeError" in error:
                result["error_type"] = "AttributeError"
            elif "<frozen importlib" in error:
                result["error_type"] = "frozen_importlib_error"
    except Exception as e:
        result["import_status"] = "exception"
        result["error"] = str(e)
    
    return result


def build_diagnostic_report() -> Dict[str, Any]:
    report = {
        "probe_id": f"smoke_import_probe_{int(time.time())}",
        "timestamp": utc_now_iso(),
        "python_version": sys.version,
        "sys_executable": sys.executable,
        "smoke_history": [],
        "recent_import_errors": [],
        "systemic_check": {},
        "protected_modules": {},
        "summary": {
            "systemic_failure": False,
            "file_specific_failures": [],
            "stale_pycache_detected": False,
            "missing_dependencies": [],
            "python_path_issues": [],
            "action_recommended": "none"
        }
    }
    
    smoke_history = query_smoke_failure_history()
    report["smoke_history"] = [
        {
            "service": r.get("service"),
            "last_heartbeat": r.get("last_heartbeat"),
            "meta": r.get("meta")
        }
        for r in smoke_history
    ]
    
    recent_errors = query_recent_import_errors()
    report["recent_import_errors"] = recent_errors
    
    systemic = diagnose_systemic_import_issues()
    report["systemic_check"] = systemic
    if systemic.get("systemic"):
        report["summary"]["systemic_failure"] = True
        report["summary"]["action_recommended"] = "systemic_python_env_issue"
    
    for module in PROTECTED_MODULES:
        diag = diagnose_protected_module(module)
        report["protected_modules"][module] = diag
        
        if diag.get("pycache_check", {}).get("has_pycache"):
            report["summary"]["stale_pycache_detected"] = True
        
        if diag.get("import_status") == "failed":
            report["summary"]["file_specific_failures"].append(module)
            if diag.get("error_type") == "frozen_importlib_error":
                report["summary"]["stale_pycache_detected"] = True
        
        for imp in diag.get("imports", []):
            resolved = resolve_import_path(imp)
            if resolved is None and imp not in report["summary"]["missing_dependencies"]:
                report["summary"]["missing_dependencies"].append(imp)
    
    if not report["summary"]["systemic_failure"]:
        if report["summary"]["stale_pycache_detected"]:
            report["summary"]["action_recommended"] = "clear_pycache"
        elif report["summary"]["file_specific_failures"]:
            report["summary"]["action_recommended"] = "module_specific_fix"
        elif report["summary"]["missing_dependencies"]:
            report["summary"]["action_recommended"] = "install_dependencies"
    
    return report


def persist_diagnostic_report(report: Dict[str, Any]) -> bool:
    meta_json = json.dumps(report, default=str)
    
    diagnostic_row = {
        "service": SERVICE_NAME,
        "status": "completed",
        "ts": utc_now_iso(),
        "meta": meta_json
    }
    
    success = ws_write("service_health", [diagnostic_row])
    
    sql = """
    CREATE TABLE IF NOT EXISTS smoke_import_diagnostics (
        probe_id VARCHAR,
        timestamp TIMESTAMPTZ,
        report_json JSON,
        systemic_failure BOOLEAN,
        file_specific_failures JSON,
        action_recommended VARCHAR
    )
    """
    try:
        requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
    except Exception as e:
        log.warning(f"Failed to create diagnostics table: {e}")
    
    insert_sql = """
    INSERT INTO smoke_import_diagnostics 
    (probe_id, timestamp, report_json, systemic_failure, file_specific_failures, action_recommended)
    VALUES (?, ?, ?, ?, ?, ?)
    """
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + "/write",
            json={
                "table": "smoke_import_diagnostics",
                "rows": [{
                    "probe_id": report["probe_id"],
                    "timestamp": report["timestamp"],
                    "report_json": report,
                    "systemic_failure": report["summary"]["systemic_failure"],
                    "file_specific_failures": report["summary"]["file_specific_failures"],
                    "action_recommended": report["summary"]["action_recommended"]
                }],
                "wait": True
            },
            timeout=30
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f"Failed to persist diagnostic: {e}")
        return False


def cycle() -> Dict[str, Any]:
    log.info("Starting smoke import failure root cause probe cycle")
    
    report = build_diagnostic_report()
    
    log.info(f"Diagnostic complete: systemic={report['summary']['systemic_failure']}, "
             f"file_failures={len(report['summary']['file_specific_failures'])}, "
             f"action={report['summary']['action_recommended']}")
    
    persist_diagnostic_report(report)
    
    return report


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None):
    meta = meta or {}
    meta["probe"] = "smoke_import_failure_root_cause_probe_v3"
    meta["last_cycle"] = utc_now_iso()
    
    row = {
        "service": SERVICE_NAME,
        "status": status,
        "ts": utc_now_iso(),
        "meta": json.dumps(meta)
    }
    ws_write("service_health", [row])


def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log.error("Another instance is running. Exiting.")
        sys.exit(1)
    
    log.info(f"{SERVICE_NAME} starting")
    send_heartbeat("starting")
    
    while True:
        try:
            report = cycle()
            send_heartbeat("running", {"report_summary": report["summary"]})
        except Exception as e:
            log.error(f"Error in cycle: {e}")
            send_heartbeat("error", {"error": str(e)})
        
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()