import os
import sys
import ast
import logging
from datetime import datetime, timezone
from typing import Optional

# --- Required Constants ---
SERVICE_NAME = "smoke_import_failure_diagnostic"
PROJECT_ROOT = "/home/workspace/zo_sentinel"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

# --- Logger setup (basicConfig once in entrypoint only) ---
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"


def ws_query(sql: str) -> list:
    """Query write_service. Returns list of rows."""
    import requests
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.warning("ws_query failed: %s", e)
        return []


def send_heartbeat():
    """Required per convention but diagnostic-only module skips heartbeat."""
    pass


def read_source(file_path: str) -> Optional[str]:
    """Read source file content safely."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error("Failed to read %s: %s", file_path, e)
        return None


def extract_line_10_imports(source: str) -> list:
    """Parse line 10 and return the import statement + any following imports on same line."""
    lines = source.split("\n")
    if len(lines) < 10:
        return []
    # Line numbers in Python AST are 1-based; line 10 is index 9
    line_10 = lines[9].strip()
    return [line_10] if line_10 else []


def get_imports_from_source(file_path: str) -> dict:
    """Walk all import nodes in a Python source file."""
    source = read_source(file_path)
    if not source:
        return {"imports": [], "error": "Could not read source"}

    imports = []
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "type": "import",
                        "module": alias.name,
                        "asname": alias.asname,
                        "line": node.lineno,
                        "col": node.col_offset,
                    })
            elif isinstance(node, ast.ImportFrom):
                imports.append({
                    "type": "import_from",
                    "module": node.module,
                    "names": [a.name for a in node.names],
                    "level": node.level,
                    "line": node.lineno,
                    "col": node.col_offset,
                })
    except SyntaxError as e:
        return {"imports": [], "error": f"SyntaxError: {e}"}

    return {"imports": imports}


def test_import_module(module_name: str) -> dict:
    """Attempt to import a module and capture the result."""
    import importlib
    try:
        mod = importlib.import_module(module_name)
        return {"module": module_name, "status": "success", "file": getattr(mod, "__file__", "<unknown>")}
    except ImportError as e:
        return {"module": module_name, "status": "failed", "error": str(e)}
    except Exception as e:
        return {"module": module_name, "status": "error", "error": str(e)}


def check_module_on_disk(module_name: str) -> dict:
    """Check if a module exists on disk and where."""
    import importlib.util

    # Check sys.modules first
    if module_name in sys.modules:
        mod = sys.modules[module_name]
        return {
            "module": module_name,
            "found": True,
            "location": "sys.modules",
            "file": getattr(mod, "__file__", "<built-in>"),
        }

    # Try spec from name
    spec = importlib.util.find_spec(module_name)
    if spec:
        return {
            "module": module_name,
            "found": True,
            "location": spec.origin or str(spec.submodule_search_locations),
            "file": spec.origin,
        }
    else:
        return {
            "module": module_name,
            "found": False,
            "location": None,
            "file": None,
        }


def diagnose_module(file_path: str, module_name: str) -> dict:
    """Full diagnostic for one failing module."""
    diagnosis = {
        "file_path": file_path,
        "module_name": module_name,
        "source_exists": os.path.exists(file_path),
        "line_10_content": None,
        "line_10_imports": [],
        "all_imports": [],
        "import_tests": [],
        "sys_path_check": [],
        "analysis": None,
    }

    source = read_source(file_path)
    if not source:
        diagnosis["analysis"] = "SOURCE_NOT_READABLE"
        return diagnosis

    # Extract line 10
    lines = source.split("\n")
    if len(lines) >= 10:
        diagnosis["line_10_content"] = lines[9]

    # Get all imports
    imports_info = get_imports_from_source(file_path)
    diagnosis["all_imports"] = imports_info.get("imports", [])
    if imports_info.get("error"):
        diagnosis["import_parse_error"] = imports_info["error"]

    # Test each import
    for imp in diagnosis["all_imports"]:
        if imp["type"] == "import":
            result = test_import_module(imp["module"])
            diagnosis["import_tests"].append(result)
        elif imp["type"] == "import_from":
            if imp["module"]:
                result = test_import_module(imp["module"])
                diagnosis["import_tests"].append(result)

    # Check sys.path for the module
    for imp in diagnosis["all_imports"]:
        if imp["type"] == "import":
            check = check_module_on_disk(imp["module"])
            diagnosis["sys_path_check"].append(check)

    # Determine failure chain
    failed_imports = [t for t in diagnosis["import_tests"] if t["status"] != "success"]
    if failed_imports:
        diagnosis["analysis"] = {
            "failure_type": "IMPORT_FAILED",
            "failed_modules": failed_imports,
            "total_imports": len(diagnosis["import_tests"]),
            "successful_imports": len([t for t in diagnosis["import_tests"] if t["status"] == "success"]),
        }
    else:
        diagnosis["analysis"] = {"failure_type": "IMPORT_MAYBE_CIRCULAR", "note": "All direct imports succeeded, possible circular dependency"}

    return diagnosis


def get_stale_daemon_report() -> dict:
    """Query service_health for stale daemons."""
    sql = """
    SELECT service, last_heartbeat,
           (EPOCH(NOW()) - EPOCH(last_heartbeat::TIMESTAMPTZ)) / 3600.0 AS age_hours
    FROM service_health
    ORDER BY age_hours DESC
    LIMIT 20
    """
    return ws_query(sql)


def format_diagnostic_report(diagnoses: list, stale_report: list) -> str:
    """Format the full diagnostic report."""
    now = datetime.now(timezone.utc).isoformat()
    report = []
    report.append("=" * 70)
    report.append(f"SMOKE IMPORT FAILURE DIAGNOSTIC REPORT")
    report.append(f"Generated: {now}")
    report.append("=" * 70)
    report.append("")

    # Stale daemon summary
    report.append("## STALE DAEMON SUMMARY")
    if stale_report:
        for entry in stale_report:
            report.append(f"  - {entry.get('service', '?')}: {entry.get('age_hours', 0):.1f}h old")
    else:
        report.append("  (No service_health data available)")
    report.append("")

    # Per-module diagnosis
    report.append("## MODULE DIAGNOSES")
    for d in diagnoses:
        report.append(f"\n### File: {d['file_path']}")
        report.append(f"  Module name: {d['module_name']}")
        report.append(f"  Source exists: {d['source_exists']}")
        report.append(f"  Line 10 content: {d['line_10_content']}")

        if d.get("import_parse_error"):
            report.append(f"  PARSE ERROR: {d['import_parse_error']}")
            continue

        all_imports = d.get("all_imports", [])
        report.append(f"  Total imports found: {len(all_imports)}")
        for imp in all_imports:
            if imp["type"] == "import":
                report.append(f"    - import {imp['module']} (line {imp['line']})")
            elif imp["type"] == "import_from":
                names = ", ".join(imp["names"])
                report.append(f"    - from {imp['module']} import {names} (line {imp['line']})")

        report.append("")
        report.append("  Import test results:")
        for test in d.get("import_tests", []):
            if test["status"] == "success":
                report.append(f"    [OK] {test['module']} -> {test['file']}")
            else:
                report.append(f"    [FAIL] {test['module']}: {test.get('error', 'unknown')}")

        report.append("")
        analysis = d.get("analysis")
        if analysis:
            if isinstance(analysis, dict):
                report.append(f"  Analysis: {analysis.get('failure_type', 'unknown')}")
                if "failed_modules" in analysis:
                    report.append("  Failed modules:")
                    for fm in analysis["failed_modules"]:
                        report.append(f"    - {fm['module']}: {fm.get('error', '?')}")
            else:
                report.append(f"  Analysis: {analysis}")

        sys_path = d.get("sys_path_check", [])
        report.append("")
        report.append("  Module resolution check:")
        for check in sys_path:
            status = "FOUND" if check["found"] else "NOT FOUND"
            loc = check.get("location", "N/A")
            report.append(f"    [{status}] {check['module']} -> {loc}")

        report.append("")
        report.append("-" * 40)

    # Root cause summary
    report.append("")
    report.append("## ROOT CAUSE SUMMARY")
    for d in diagnoses:
        analysis = d.get("analysis")
        if analysis and isinstance(analysis, dict):
            failure_type = analysis.get("failure_type", "unknown")
            failed = analysis.get("failed_modules", [])
            if failed:
                for fm in failed:
                    report.append(f"  {d['module_name']}: {failure_type} -> {fm['module']}: {fm.get('error', '?')}")
            else:
                report.append(f"  {d['module_name']}: {failure_type} (no direct import failure)")
        else:
            report.append(f"  {d['module_name']}: {analysis or 'UNABLE TO DETERMINE'}")

    report.append("")
    report.append("=" * 70)
    return "\n".join(report)


def get_smoke_log_for_module(module_name: str) -> list:
    """Read recent lines from smoke_test output for a given module."""
    # This reads from the smoke test log if available
    smoke_log_path = os.path.join(LOG_DIR, "smoke_test.log")
    matches = []
    if os.path.exists(smoke_log_path):
        try:
            with open(smoke_log_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Find sections mentioning this module
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if module_name in line and ("import" in line.lower() or "line 10" in line.lower() or "frozen importlib" in line.lower()):
                    # Grab context window
                    start = max(0, i - 2)
                    end = min(len(lines), i + 10)
                    matches.extend(lines[start:end])
        except Exception as e:
            logger.warning("Could not read smoke log: %s", e)
    return matches


def run() -> dict:
    """Run the full diagnostic."""
    logger.info("Starting smoke import failure diagnostic")

    # Modules to diagnose
    targets = [
        ("registry_api", "/home/workspace/zo_sentinel/registry_api.py"),
        ("rug_pull_monitor", "/home/workspace/zo_sentinel/rug_pull_monitor.py"),
        ("signal_analyser", "/home/workspace/zo_sentinel/signal_analyser.py"),
    ]

    diagnoses = []
    for module_name, file_path in targets:
        logger.info("Diagnosing %s at %s", module_name, file_path)
        diagnosis = diagnose_module(file_path, module_name)
        diagnoses.append(diagnosis)

    # Get stale daemon report
    stale_report = get_stale_daemon_report()

    # Format and print report
    report = format_diagnostic_report(diagnoses, stale_report)
    print(report)

    # Also write to log file
    logger.info("Diagnostic complete for %d modules", len(diagnoses))

    # Return structured result
    return {
        "diagnoses": diagnoses,
        "stale_services": stale_report,
        "report": report,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
        ],
    )
    result = run()
    sys.exit(0)