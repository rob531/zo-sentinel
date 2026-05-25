#!/usr/bin/env python3
"""
importlib_failure_diagnostic.py

Diagnostic probe for importlib failures in protected modules.
Inspects registry_api.py, signal_analyser.py, rug_pull_monitor.py for
ImportError/ModuleNotFoundError patterns. Parses top-level imports,
identifies missing dependencies, and writes findings to service_diagnostics
table via write_service.

Protected files cannot be rebuilt; this diagnostic provides actionable
information for operator resolution.

No DB writes except to service_diagnostics.
"""

import logging
import sys
import os
import importlib
import inspect
import traceback
import ast
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any
from pathlib import Path
import requests

# Configuration
SERVICE_NAME = "importlib_failure_diagnostic"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
PROJECT_ROOT = "/home/workspace/zo_sentinel"

# Protected modules that cannot be rebuilt
PROTECTED_MODULES = [
    "registry_api",
    "signal_analyser",
    "rug_pull_monitor"
]

# Module source file mapping
MODULE_FILES = {
    "registry_api": "registry_api.py",
    "signal_analyser": "signal_analyser.py",
    "rug_pull_monitor": "rug_pull_monitor.py"
}

# Module-level logger
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.DEBUG)

LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")
os.makedirs(LOG_DIR, exist_ok=True)

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


def log_info(msg: str):
    """Log info message."""
    logger.info(msg)
    print(f"[INFO] {msg}", flush=True)


def log_error(msg: str):
    """Log error message."""
    logger.error(msg)
    print(f"[ERROR] {msg}", flush=True)


def ws_query(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service."""
    try:
        payload = {"sql": sql, "params": list(params) if params else []}
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            result = resp.json()
            return result.get("rows", [])
        else:
            log_error(f"Query failed: {resp.status_code} - {resp.text}")
            return []
    except Exception as e:
        log_error(f"Query error: {e}")
        return []


def ws_execute(sql: str, params: Optional[tuple] = None) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        payload = {"sql": sql, "params": list(params) if params else [], "wait": True}
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/execute",
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            return True
        else:
            log_error(f"Execute failed: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        log_error(f"Execute error: {e}")
        return False


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to table via write_service."""
    try:
        payload = {"table": table, "rows": rows, "wait": True}
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            return True
        else:
            log_error(f"Write failed: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        log_error(f"Write error: {e}")
        return False


def check_already_built() -> Set[str]:
    """Read ALREADY_BUILT list to confirm protected status."""
    already_built = set()
    already_built_path = os.path.join(PROJECT_ROOT, "ALREADY_BUILT.txt")
    
    if os.path.exists(already_built_path):
        try:
            with open(already_built_path, 'r') as f:
                for line in f:
                    module = line.strip()
                    if module and not module.startswith('#'):
                        already_built.add(module)
            log_info(f"Found {len(already_built)} modules in ALREADY_BUILT.txt")
        except Exception as e:
            log_error(f"Failed to read ALREADY_BUILT.txt: {e}")
    
    return already_built


def parse_imports_from_source(file_path: str) -> Dict[str, List[str]]:
    """Parse top-level imports from source file using AST."""
    imports = {
        "import_statements": [],
        "from_imports": [],
        "import_errors": []
    }
    
    if not os.path.exists(file_path):
        imports["import_errors"].append(f"File not found: {file_path}")
        return imports
    
    try:
        with open(file_path, 'r') as f:
            source = f.read()
        
        tree = ast.parse(source, filename=file_path)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports["import_statements"].append({
                        "module": alias.name,
                        "asname": alias.asname,
                        "line": node.lineno
                    })
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports["from_imports"].append({
                        "module": module,
                        "name": alias.name,
                        "asname": alias.asname,
                        "level": node.level,
                        "line": node.lineno
                    })
        
    except SyntaxError as e:
        imports["import_errors"].append(f"Syntax error: {e}")
    except Exception as e:
        imports["import_errors"].append(f"Parse error: {e}")
    
    return imports


def check_import_resolution(imports: Dict[str, List[Dict]]) -> List[Dict[str, Any]]:
    """Check if each import can be resolved."""
    results = []
    
    # Check import statements
    for imp in imports.get("import_statements", []):
        module_name = imp["module"]
        result = {
            "type": "import",
            "module": module_name,
            "line": imp["line"],
            "status": "unknown",
            "error": None
        }
        
        try:
            # Try to import the module
            importlib.import_module(module_name)
            result["status"] = "ok"
        except ModuleNotFoundError as e:
            result["status"] = "missing"
            result["error"] = str(e)
        except ImportError as e:
            result["status"] = "import_error"
            result["error"] = str(e)
        except Exception as e:
            result["status"] = "other_error"
            result["error"] = str(e)
        
        results.append(result)
    
    # Check from imports
    for imp in imports.get("from_imports", []):
        module_name = imp["module"]
        name = imp["name"]
        
        if imp["level"] > 0:
            # Relative import - skip
            continue
        
        result = {
            "type": "from_import",
            "module": module_name,
            "name": name,
            "line": imp["line"],
            "status": "unknown",
            "error": None
        }
        
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, name):
                result["status"] = "ok"
            else:
                result["status"] = "missing_attr"
                result["error"] = f"Module '{module_name}' has no attribute '{name}'"
        except ModuleNotFoundError as e:
            result["status"] = "missing"
            result["error"] = str(e)
        except ImportError as e:
            result["status"] = "import_error"
            result["error"] = str(e)
        except Exception as e:
            result["status"] = "other_error"
            result["error"] = str(e)
        
        results.append(result)
    
    return results


def diagnose_module(module_name: str, source_file: str) -> Dict[str, Any]:
    """Run complete diagnostic on a single module."""
    diagnosis = {
        "module": module_name,
        "source_file": source_file,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "unknown",
        "imports_parsed": 0,
        "imports_resolved": 0,
        "imports_missing": 0,
        "import_errors": [],
        "findings": []
    }
    
    file_path = os.path.join(PROJECT_ROOT, source_file)
    log_info(f"Diagnosing {module_name} at {file_path}")
    
    # Parse imports
    imports = parse_imports_from_source(file_path)
    diagnosis["imports_parsed"] = len(imports.get("import_statements", [])) + len(imports.get("from_imports", []))
    diagnosis["import_errors"] = imports.get("import_errors", [])
    
    if imports.get("import_errors"):
        diagnosis["status"] = "parse_error"
        return diagnosis
    
    # Check import resolution
    resolution_results = check_import_resolution(imports)
    
    for result in resolution_results:
        if result["status"] == "ok":
            diagnosis["imports_resolved"] += 1
        elif result["status"] in ("missing", "import_error"):
            diagnosis["imports_missing"] += 1
            diagnosis["findings"].append({
                "type": "missing_import",
                "line": result["line"],
                "module": result["module"],
                "name": result.get("name"),
                "error": result["error"]
            })
        elif result["status"] == "other_error":
            diagnosis["findings"].append({
                "type": "other_import_error",
                "line": result["line"],
                "module": result["module"],
                "name": result.get("name"),
                "error": result["error"]
            })
    
    # Determine overall status
    if diagnosis["imports_missing"] > 0:
        diagnosis["status"] = "missing_imports"
    elif diagnosis["findings"]:
        diagnosis["status"] = "import_errors"
    else:
        diagnosis["status"] = "ok"
    
    return diagnosis


def check_service_diagnostics_table() -> bool:
    """Check if service_diagnostics table exists."""
    sql = "SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_name = 'service_diagnostics'"
    result = ws_query(sql)
    return len(result) > 0 and result[0].get("cnt", 0) > 0


def ensure_service_diagnostics_table() -> bool:
    """Ensure service_diagnostics table exists with required schema."""
    # Check if table exists
    if not check_service_diagnostics_table():
        sql = """
        CREATE SEQUENCE IF NOT EXISTS service_diagnostics_id_seq
        """
        if not ws_execute(sql):
            return False
        
        sql = """
        CREATE TABLE IF NOT EXISTS service_diagnostics (
            id INTEGER PRIMARY KEY DEFAULT nextval('service_diagnostics_id_seq'),
            service_name VARCHAR,
            diagnostic_type VARCHAR,
            module_name VARCHAR,
            status VARCHAR,
            findings JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        if ws_execute(sql):
            log_info("Created service_diagnostics table")
            return True
        return False
    return True


def write_diagnosis_to_db(diagnosis: Dict[str, Any]) -> bool:
    """Write diagnostic findings to service_diagnostics table."""
    rows = [{
        "service_name": SERVICE_NAME,
        "diagnostic_type": "importlib_failure",
        "module_name": diagnosis["module"],
        "status": diagnosis["status"],
        "findings": diagnosis
    }]
    
    return ws_write("service_diagnostics", rows)


def generate_summary_report(all_diagnoses: List[Dict[str, Any]], already_built: Set[str]) -> str:
    """Generate human-readable summary report."""
    lines = [
        "=" * 70,
        "IMPORTLIB FAILURE DIAGNOSTIC REPORT",
        "=" * 70,
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Project: {PROJECT_ROOT}",
        "",
        "PROTECTED MODULES STATUS:",
        "-" * 70
    ]
    
    protected_count = 0
    ok_count = 0
    problem_count = 0
    
    for diag in all_diagnoses:
        module = diag["module"]
        status = diag["status"]
        is_protected = module in already_built
        
        status_icon = "✅" if status == "ok" else "⚠️" if status == "missing_imports" else "❌"
        protected_label = " [PROTECTED]" if is_protected else ""
        
        lines.append(f"{status_icon} {module}{protected_label}")
        lines.append(f"    Status: {status}")
        lines.append(f"    Imports parsed: {diag.get('imports_parsed', 0)}")
        lines.append(f"    Imports resolved: {diag.get('imports_resolved', 0)}")
        lines.append(f"    Imports missing: {diag.get('imports_missing', 0)}")
        
        if diag.get("findings"):
            lines.append("    Findings:")
            for finding in diag["findings"]:
                lines.append(f"      - {finding.get('type')}: {finding.get('module')} (line {finding.get('line')})")
                if finding.get("error"):
                    lines.append(f"        Error: {finding['error'][:100]}")
        
        lines.append("")
        
        if is_protected:
            protected_count += 1
        if status == "ok":
            ok_count += 1
        else:
            problem_count += 1
    
    lines.append("-" * 70)
    lines.append("SUMMARY:")
    lines.append(f"  Total modules diagnosed: {len(all_diagnoses)}")
    lines.append(f"  Protected modules: {protected_count}")
    lines.append(f"  OK: {ok_count}")
    lines.append(f"  With problems: {problem_count}")
    lines.append("")
    
    if problem_count > 0:
        lines.append("ACTION REQUIRED:")
        lines.append("  Protected modules have import failures that cannot be")
        lines.append("  auto-remediated. Manual intervention may be required.")
        lines.append("")
        lines.append("  Possible remediation steps:")
        lines.append("  1. Install missing packages: pip install <package>")
        lines.append("  2. Check for circular imports in local modules")
        lines.append("  3. Verify sys.path includes all required directories")
        lines.append("  4. Consider adding stubs for missing dependencies")
    
    lines.append("=" * 70)
    
    return "\n".join(lines)


def run() -> Dict[str, Any]:
    """Main execution function."""
    log_info("=" * 60)
    log_info("Starting importlib failure diagnostic")
    log_info("=" * 60)
    
    results = {
        "success": True,
        "diagnoses": [],
        "summary": {}
    }
    
    # Check ALREADY_BUILT list
    already_built = check_already_built()
    log_info(f"Found {len(already_built)} modules in ALREADY_BUILT.txt")
    
    # Ensure service_diagnostics table exists
    if not ensure_service_diagnostics_table():
        log_error("Failed to ensure service_diagnostics table exists")
        results["success"] = False
        return results
    
    # Run diagnosis on each protected module
    for module_name in PROTECTED_MODULES:
        source_file = MODULE_FILES.get(module_name)
        if not source_file:
            log_error(f"No source file mapping for {module_name}")
            continue
        
        diagnosis = diagnose_module(module_name, source_file)
        results["diagnoses"].append(diagnosis)
        
        # Write to DB
        if write_diagnosis_to_db(diagnosis):
            log_info(f"Wrote diagnosis for {module_name} to service_diagnostics")
        else:
            log_error(f"Failed to write diagnosis for {module_name}")
        
        # Log summary
        log_info(f"{module_name}: {diagnosis['status']} "
                f"(parsed={diagnosis['imports_parsed']}, "
                f"resolved={diagnosis['imports_resolved']}, "
                f"missing={diagnosis['imports_missing']})")
    
    # Generate summary report
    report = generate_summary_report(results["diagnoses"], already_built)
    print("\n" + report)
    
    # Write report to file
    report_file = os.path.join(LOG_DIR, f"{SERVICE_NAME}_report.txt")
    try:
        with open(report_file, 'w') as f:
            f.write(report)
        log_info(f"Report written to {report_file}")
    except Exception as e:
        log_error(f"Failed to write report file: {e}")
    
    # Count problems
    problem_modules = [d for d in results["diagnoses"] if d["status"] not in ("ok",)]
    results["summary"] = {
        "total": len(results["diagnoses"]),
        "ok": len([d for d in results["diagnoses"] if d["status"] == "ok"]),
        "problems": len(problem_modules),
        "problem_modules": [d["module"] for d in problem_modules]
    }
    
    log_info("=" * 60)
    log_info(f"Diagnostic complete. Problems found: {results['summary']['problems']}")
    log_info("=" * 60)
    
    return results


if __name__ == "__main__":
    results = run()
    sys.exit(0 if results["success"] else 1)