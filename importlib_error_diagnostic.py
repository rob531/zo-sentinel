#!/usr/bin/env python3
"""
importlib_error_diagnostic.py
Diagnostic daemon to investigate repeated importlib errors in protected files.
Monitors: registry_api.py, rug_pull_monitor.py, signal_analyser.py
"""

import ast
import hashlib
import importlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

SERVICE_NAME = "importlib_error_diagnostic"
SERVICE_PORT = None
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = "/home/workspace/zo_sentinel/var/run/importlib_error_diagnostic.pid"
LOG_DIR = Path("/home/workspace/logs")

PROTECTED_FILES = [
    "/home/workspace/zo_sentinel/registry_api.py",
    "/home/workspace/zo_sentinel/rug_pull_monitor.py",
    "/home/workspace/zo_sentinel/signal_analyser.py",
]

PROTECTED_FILE_HASHES = {
    "/home/workspace/zo_sentinel/registry_api.py": None,
    "/home/workspace/zo_sentinel/rug_pull_monitor.py": None,
    "/home/workspace/zo_sentinel/signal_analyser.py": None,
}

logger = logging.getLogger(__name__)
running = True


def ws_write(table: str, rows: List[Dict]) -> bool:
    """Wrapper for write_service POST."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=10
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_write failed: {e}")
        return False


def ws_query(sql: str) -> List[Dict]:
    """Wrapper for write_service query."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"sql": sql, "wait": True},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json().get("rows", [])
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        return []


def send_heartbeat(status: str = "running", meta: Optional[Dict] = None):
    """Send heartbeat to service_health table."""
    row = {
        "service_name": SERVICE_NAME,
        "status": status,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "meta": json.dumps(meta or {})
    }
    ws_write("service_health", [row])


def check_single_instance():
    """Ensure only one instance runs."""
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            os.kill(old_pid, 0)
            logger.error(f"Instance already running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except (OSError, ValueError):
            pass
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))


def remove_pid_file():
    """Remove PID file on shutdown."""
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Failed to remove PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global running
    logger.info(f"Received signal {signum}, initiating graceful shutdown")
    running = False


def compute_file_hash(filepath: str) -> Optional[str]:
    """Compute SHA256 hash of file contents."""
    try:
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def check_syntax(filepath: str) -> Tuple[bool, Optional[str]]:
    """Validate Python syntax via ast.parse."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        ast.parse(source)
        return True, None
    except SyntaxError as e:
        return False, f"SyntaxError line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"Parse error: {e}"


def extract_imports_from_ast(filepath: str) -> List[str]:
    """Extract import module names from AST."""
    imports = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
    except Exception as e:
        logger.warning(f"Failed to extract imports from {filepath}: {e}")
    return imports


def try_import_via_subprocess(module_script: str, timeout: int = 30) -> Tuple[bool, str, str]:
    """Try importing via subprocess to capture full traceback."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", module_script],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        success = result.returncode == 0
        return success, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "TIMEOUT: Import took longer than 30 seconds"
    except Exception as e:
        return False, "", f"Subprocess error: {e}"


def parse_frozen_importlib_error(error_text: str) -> Dict[str, Any]:
    """Parse traceback looking for frozen importlib pattern."""
    info = {
        "frozen_importlib_detected": False,
        "line_10_pattern": False,
        "file_in_traceback": None,
        "error_type": None,
        "error_message": None,
        "module_stack": []
    }
    
    if "frozen importlib" in error_text:
        info["frozen_importlib_detected"] = True
    
    if re.search(r"line\s*10\b", error_text, re.IGNORECASE):
        info["line_10_pattern"] = True
    
    file_match = re.search(r'File "([^"]+)"', error_text)
    if file_match:
        info["file_in_traceback"] = file_match.group(1)
    
    type_match = re.search(r"(\w+Error):\s*(.+?)(?=\n|$)", error_text, re.DOTALL)
    if type_match:
        info["error_type"] = type_match.group(1)
        info["error_message"] = type_match.group(2).strip()
    
    for match in re.finditer(r'File "([^"]+)"', error_text):
        info["module_stack"].append(match.group(1))
    
    return info


def run_subprocess_import_probe(filepath: str) -> Dict[str, Any]:
    """Run subprocess import to capture detailed error."""
    probe_script = f"""
import sys
import traceback
sys.path.insert(0, '/home/workspace/zo_sentinel')
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("target_module", "{filepath}")
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        print("ERROR: Could not create spec or loader")
except Exception:
    traceback.print_exc()
"""
    
    success, stdout, stderr = try_import_via_subprocess(probe_script)
    
    result = {
        "success": success,
        "stdout": stdout[:500] if stdout else None,
        "stderr": stderr[:2000] if stderr else None,
        "traceback_analysis": {}
    }
    
    if stderr:
        result["traceback_analysis"] = parse_frozen_importlib_error(stderr)
    
    return result


def diagnose_protected_file(filepath: str) -> Dict[str, Any]:
    """Run full diagnostic on a protected file."""
    diag = {
        "filepath": filepath,
        "filename": os.path.basename(filepath),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file_exists": False,
        "file_size": None,
        "file_hash": None,
        "hash_changed": False,
        "previous_hash": None,
        "syntax_valid": False,
        "syntax_error": None,
        "imports_extracted": [],
        "subprocess_result": {},
        "diagnostic_status": "unknown",
        "recommendations": []
    }
    
    if not os.path.exists(filepath):
        diag["diagnostic_status"] = "file_not_found"
        diag["recommendations"].append("File does not exist - may have been deleted or moved")
        return diag
    
    diag["file_exists"] = True
    
    try:
        stat = os.stat(filepath)
        diag["file_size"] = stat.st_size
    except Exception as e:
        diag["recommendations"].append(f"Could not stat file: {e}")
    
    current_hash = compute_file_hash(filepath)
    diag["file_hash"] = current_hash
    
    prev_hash = PROTECTED_FILE_HASHES.get(filepath)
    if prev_hash is None:
        PROTECTED_FILE_HASHES[filepath] = current_hash
        diag["previous_hash"] = None
        diag["hash_changed"] = None
    elif prev_hash != current_hash:
        diag["previous_hash"] = prev_hash
        diag["hash_changed"] = True
        diag["recommendations"].append("File content has changed since last check")
        PROTECTED_FILE_HASHES[filepath] = current_hash
    else:
        diag["previous_hash"] = prev_hash
        diag["hash_changed"] = False
    
    syntax_ok, syntax_err = check_syntax(filepath)
    diag["syntax_valid"] = syntax_ok
    diag["syntax_error"] = syntax_err
    
    if not syntax_ok:
        diag["diagnostic_status"] = "syntax_error"
        diag["recommendations"].append(f"Fix Python syntax errors: {syntax_err}")
        return diag
    
    imports = extract_imports_from_ast(filepath)
    diag["imports_extracted"] = imports[:20]
    
    if imports:
        diag["recommendations"].append(f"File imports {len(imports)} modules - verify all are installed")
    
    diag["subprocess_result"] = run_subprocess_import_probe(filepath)
    
    if not diag["subprocess_result"]["success"]:
        diag["diagnostic_status"] = "import_failed"
        tb_analysis = diag["subprocess_result"].get("traceback_analysis", {})
        
        if tb_analysis.get("frozen_importlib_detected"):
            diag["recommendations"].append("frozen importlib error detected - possible .pyc corruption or bytecode cache issue")
        if tb_analysis.get("line_10_pattern"):
            diag["recommendations"].append("Error at line 10 pattern - check for import cycle or corrupted header")
        if tb_analysis.get("error_type"):
            diag["recommendations"].append(f"Root error type: {tb_analysis['error_type']}")
        
        diag["error_type"] = tb_analysis.get("error_type")
        diag["error_message"] = tb_analysis.get("error_message")
    else:
        diag["diagnostic_status"] = "import_ok"
    
    return diag


def store_diagnostic_results(results: List[Dict]):
    """Store diagnostic results to DuckDB via write_service."""
    for result in results:
        diag_record = {
            "service_name": SERVICE_NAME,
            "filepath": result["filepath"],
            "filename": result["filename"],
            "diagnostic_status": result["diagnostic_status"],
            "file_exists": result["file_exists"],
            "syntax_valid": result["syntax_valid"],
            "frozen_importlib_detected": result.get("subprocess_result", {}).get("traceback_analysis", {}).get("frozen_importlib_detected", False),
            "line_10_pattern": result.get("subprocess_result", {}).get("traceback_analysis", {}).get("line_10_pattern", False),
            "error_type": result.get("error_type"),
            "error_message": result.get("error_message"),
            "diagnostic_timestamp": result["timestamp"],
            "recommendations": json.dumps(result.get("recommendations", []))
        }
        ws_write("importlib_diagnostics", [diag_record])


def cycle():
    """Perform one diagnostic cycle."""
    logger.info("Starting importlib error diagnostic cycle")
    
    results = []
    for filepath in PROTECTED_FILES:
        logger.info(f"Diagnosing {filepath}")
        result = diagnose_protected_file(filepath)
        results.append(result)
        
        logger.info(f"  Status: {result['diagnostic_status']}")
        if result.get("syntax_error"):
            logger.error(f"  Syntax: {result['syntax_error']}")
        if result.get("subprocess_result", {}).get("traceback_analysis", {}).get("frozen_importlib_detected"):
            logger.warning(f"  Frozen importlib error detected!")
        if result.get("subprocess_result", {}).get("traceback_analysis", {}).get("line_10_pattern"):
            logger.warning(f"  Line 10 error pattern detected!")
        
        for rec in result.get("recommendations", []):
            logger.info(f"  Recommendation: {rec}")
    
    store_diagnostic_results(results)
    
    failed_count = sum(1 for r in results if r["diagnostic_status"] != "import_ok")
    logger.info(f"Diagnostic cycle complete. {len(results)} files checked, {failed_count} with issues.")
    
    return results


def run():
    """Main daemon loop."""
    global running
    
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[logging.FileHandler(LOG_DIR / f"{SERVICE_NAME}.log")]
    )
    
    logger.info(f"Starting {SERVICE_NAME}")
    
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        cycle_count = 0
        while running:
            cycle_count += 1
            
            try:
                cycle()
            except Exception as e:
                logger.error(f"Error in diagnostic cycle: {e}", exc_info=True)
            
            send_heartbeat(
                status="running",
                meta={
                    "cycle_count": cycle_count,
                    "files_monitored": len(PROTECTED_FILES)
                }
            )
            
            for _ in range(60):
                if not running:
                    break
                time.sleep(1)
    
    except Exception as e:
        logger.error(f"Fatal error in run loop: {e}", exc_info=True)
        send_heartbeat(status="fatal_error", meta={"error": str(e)})
    finally:
        remove_pid_file()
        logger.info(f"{SERVICE_NAME} shut down complete")


if __name__ == "__main__":
    run()