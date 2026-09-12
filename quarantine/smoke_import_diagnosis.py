#!/usr/bin/env python3
"""
smoke_import_diagnosis.py -- ZO-SENTINEL Phase 6
Diagnostic module to identify root cause of smoke failures in registry_api.py,
rug_pull_monitor.py, and signal_analyser.py.

The files show identical traceback pattern:
  File "<string>", line 10, in <module>
  File "<frozen importlib"... Query write_service to inspect recent smoke test logs
and identify which import statement at line 10 is failing in each module.
"""

import ast
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "smoke_import_diagnosis"
SERVICE_PORT = None  # one-shot script, not a daemon
WRITE_SERVICE_URL = "http://localhost:8772"
LOG_FILE = "/home/workspace/logs/smoke_import_diagnosis.log"

TARGET_FILES = [
    "/home/workspace/zo_sentinel/registry_api.py",
    "/home/workspace/zo_sentinel/rug_pull_monitor.py",
    "/home/workspace/zo_sentinel/signal_analyser.py",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: list = None) -> list:
    """Query DuckDB via write_service REST API."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "error":
        raise RuntimeError(f"Query error: {data.get('error')}")
    return data.get("rows", [])


def ws_write(table: str, rows: list) -> dict:
    """Write rows to DuckDB via write_service REST API."""
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_line_10_imports(filepath: str) -> list:
    """Parse Python file and extract all import statements up to line 10."""
    imports = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            lineno = getattr(node, "lineno", 0)
            if lineno == 10:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(f"import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = ", ".join(a.name for a in node.names)
                    imports.append(f"from {module} import {names}")
    except Exception as e:
        logger.warning("Could not parse %s: %s", filepath, e)
    return imports


def query_smoke_logs(limit: int = 50) -> list:
    """Query recent smoke test logs from DuckDB."""
    sql = """
    SELECT 
        test_name,
        status,
        error_message,
        recent_errors,
        started_at,
        completed_at
    FROM smoke_test_logs
    ORDER BY started_at DESC
    LIMIT ?
    """
    return ws_query(sql, [limit])


def diagnose_import_failure(filepath: str) -> dict:
    """Diagnose a single file's import issues."""
    result = {
        "file": filepath,
        "line_10_imports": [],
        "file_exists": os.path.exists(filepath),
        "can_parse": False,
        "syntax_errors": [],
        "likely_problem": None,
    }

    if not result["file_exists"]:
        result["likely_problem"] = "FILE_NOT_FOUND"
        return result

    imports = get_line_10_imports(filepath)
    result["line_10_imports"] = imports

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        ast.parse(source)
        result["can_parse"] = True
    except SyntaxError as e:
        result["syntax_errors"].append(str(e))
        result["likely_problem"] = f"SYNTAX_ERROR: {e}"

    # Check for common problematic imports at line 10
    problematic = []
    for imp in imports:
        if "torch" in imp.lower() or "tensorflow" in imp.lower():
            problematic.append(f"{imp} (GPU/ML library - may not be installed)")
        elif "cv2" in imp.lower() or "opencv" in imp.lower():
            problematic.append(f"{imp} (OpenCV - may not be installed)")
        elif "spacy" in imp.lower():
            problematic.append(f"{imp} (spaCy - may not be installed)")
        elif "transformers" in imp.lower():
            problematic.append(f"{imp} (HuggingFace - may not be installed)")

    if problematic:
        result["likely_problem"] = "MISSING_DEPENDENCY"
        result["problematic_imports"] = problematic

    return result


def query_recent_smoke_failures() -> list:
    """Query for recent smoke test failures from service_health or related tables."""
    sql = """
    SELECT 
        service_name,
        status,
        last_heartbeat,
        meta
    FROM service_health
    WHERE service_name LIKE '%smoke%' 
       OR service_name LIKE '%registry%'
       OR service_name LIKE '%rug%'
       OR service_name LIKE '%signal%'
    ORDER BY last_heartbeat DESC
    LIMIT 20
    """
    try:
        return ws_query(sql)
    except Exception as e:
        logger.warning("Could not query service_health: %s", e)
        return []


def check_module_importability(module_path: str) -> dict:
    """Attempt to import a module and capture any errors."""
    result = {"path": module_path, "importable": False, "error": None}
    
    import importlib.util
    spec = importlib.util.spec_from_file_location("test_module", module_path)
    if spec and spec.loader:
        try:
            module = importlib.util.module_from_spec(spec)
            importlib.util.increment_try_count(module)
            result["importable"] = True
        except Exception as e:
            result["importable"] = False
            result["error"] = str(e)
    return result


def main():
    """Run diagnostic analysis on target files."""
    logger.info("=" * 60)
    logger.info("Starting smoke import diagnosis at %s", datetime.now(timezone.utc).isoformat())
    logger.info("=" * 60)

    diagnostics = []
    
    for filepath in TARGET_FILES:
        logger.info("Diagnosing: %s", filepath)
        diag = diagnose_import_failure(filepath)
        diagnostics.append(diag)
        
        logger.info("  File exists: %s", diag["file_exists"])
        logger.info("  Can parse AST: %s", diag["can_parse"])
        logger.info("  Line 10 imports: %s", diag["line_10_imports"])
        if diag.get("problematic_imports"):
            logger.info("  Problematic imports: %s", diag["problematic_imports"])
        if diag.get("likely_problem"):
            logger.info("  Likely problem: %s", diag["likely_problem"])

    # Query smoke test logs
    logger.info("\nQuerying recent smoke test logs...")
    try:
        smoke_logs = query_smoke_logs(limit=20)
        logger.info("Found %d recent smoke log entries", len(smoke_logs))
        for log in smoke_logs:
            logger.info("  Test: %s, Status: %s", log.get("test_name"), log.get("status"))
    except Exception as e:
        logger.warning("Could not query smoke logs: %s", e)

    # Query service health for related services
    logger.info("\nQuerying service health for related services...")
    try:
        health = query_recent_smoke_failures()
        logger.info("Found %d related health entries", len(health))
    except Exception as e:
        logger.warning("Could not query service health: %s", e)

    # Generate summary
    summary = {
        "diagnostic_run_at": datetime.now(timezone.utc).isoformat(),
        "files_checked": len(TARGET_FILES),
        "diagnostics": diagnostics,
        "root_cause_hypothesis": None,
    }

    # Identify common pattern across all three files
    all_have_problem = all(d.get("likely_problem") == "MISSING_DEPENDENCY" for d in diagnostics)
    if all_have_problem:
        all_imports = []
        for d in diagnostics:
            all_imports.extend(d.get("problematic_imports", []))
        unique_imports = list(set(all_imports))
        summary["root_cause_hypothesis"] = {
            "type": "COMMON_MISSING_DEPENDENCY",
            "affected_files": len(diagnostics),
            "common_issue": "All three files likely share a missing dependency",
            "recommendation": "Check if the problematic imports are installed in the build environment",
            "problematic_imports": unique_imports,
        }
    else:
        summary["root_cause_hypothesis"] = {
            "type": "MIXED_ISSUES",
            "affected_files": len(diagnostics),
            "recommendation": "Each file may have unique issues - review individual diagnostics above",
        }

    logger.info("\n" + "=" * 60)
    logger.info("DIAGNOSIS SUMMARY")
    logger.info("=" * 60)
    logger.info("Root cause hypothesis: %s", summary["root_cause_hypothesis"])
    logger.info("=" * 60)

    # Write results to diagnostics table
    try:
        ws_write("smoke_import_diagnostics", [{
            "diagnostic_run_at": summary["diagnostic_run_at"],
            "files_checked": summary["files_checked"],
            "root_cause": str(summary["root_cause_hypothesis"]),
            "diagnostic_json": str(summary),
        }])
        logger.info("Results written to smoke_import_diagnostics table")
    except Exception as e:
        logger.warning("Could not write diagnostic results: %s", e)

    logger.info("Diagnosis complete at %s", datetime.now(timezone.utc).isoformat())
    sys.exit(0)


if __name__ == "__main__":
    main()