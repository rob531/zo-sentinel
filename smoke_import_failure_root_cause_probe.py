import ast
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

SERVICE_NAME = "smoke_import_failure_root_cause_probe"
LOG_FILE = "/home/workspace/logs/smoke_import_failure_root_cause_probe.log"

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"

PROTECTED_FILES = [
    "/home/workspace/zo_sentinel/registry_api.py",
    "/home/workspace/zo_sentinel/rug_pull_monitor.py",
    "/home/workspace/zo_sentinel/signal_analyser.py",
]

SIGNAL_ANALYSER_HIERARCHY = [
    "/home/workspace/zo_sentinel/signal_analyser.py",
    "/home/workspace/zo_sentinel/trust_synthesiser_v2.py",
    "/home/workspace/zo_sentinel/signal_bridge.py",
    "/home/workspace/zo_sentinel/attestation_engine.py",
    "/home/workspace/zo_sentinel/mcp_scanner.py",
]

WORKSPACE_PATHS = [
    "/home/workspace/zo_sentinel",
    "/home/workspace/zo_mesh",
    "/home/workspace",
]

EXPECTED_SYMBOLS = {
    "registry_api": ["ws_write", "ws_query", "send_heartbeat", "check_single_instance"],
    "rug_pull_monitor": ["ws_write", "ws_query", "send_heartbeat", "check_single_instance"],
    "signal_analyser": ["ws_write", "ws_query", "send_heartbeat", "check_single_instance"],
}

def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write service."""
    import requests
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=5)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []

def ws_write(table: str, rows: list[dict[str, Any]]) -> bool:
    """Write to write service."""
    import requests
    try:
        resp = requests.post(WRITE_SERVICE_URL + "/write", json={"table": table, "rows": rows}, timeout=5)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False

def send_heartbeat(status: str = "running", meta: dict[str, Any] | None = None) -> None:
    """Send heartbeat to service_health."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    row = {"service": SERVICE_NAME, "status": status, "ts": ts, "meta": meta or {}}
    ws_write("service_health", [row])

def check_sys_path_entries() -> dict[str, Any]:
    """Check if sys.path contains expected workspace paths."""
    result = {
        "checked_paths": [],
        "missing_paths": [],
        "present_paths": [],
    }
    for path in WORKSPACE_PATHS:
        if path in sys.path:
            result["present_paths"].append(path)
        else:
            result["missing_paths"].append(path)
        result["checked_paths"].append({"path": path, "present": path in sys.path})
    return result

def extract_imports_from_source(file_path: str) -> list[dict[str, Any]]:
    """Extract all import statements from a Python source file."""
    imports = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "type": "import",
                        "module": alias.name,
                        "alias": alias.asname,
                        "line": node.lineno,
                    })
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append({
                        "type": "import_from",
                        "module": module,
                        "name": alias.name,
                        "alias": alias.asname,
                        "level": node.level,
                        "line": node.lineno,
                    })
    except Exception as e:
        log.error(f"Failed to parse {file_path}: {e}")
    return imports

def check_module_importable(module_name: str) -> dict[str, Any]:
    """Check if a module can be imported using importlib."""
    result = {
        "module_name": module_name,
        "spec_found": False,
        "spec_location": None,
        "can_import": False,
        "error": None,
    }
    try:
        spec = importlib.util.find_spec(module_name)
        if spec:
            result["spec_found"] = True
            result["spec_location"] = str(spec.origin) if spec.origin else None
            loader = importlib.util.LazyLoader(spec.loader) if spec.loader else None
            if spec.loader:
                result["can_import"] = True
            else:
                result["error"] = "No loader available"
        else:
            result["error"] = "Module spec not found"
    except Exception as e:
        result["error"] = str(e)
    return result

def check_import_chain(file_path: str, visited: set[str] | None = None) -> dict[str, Any]:
    """Walk import chain to detect circular dependencies."""
    if visited is None:
        visited = set()
    
    result = {
        "file": file_path,
        "chain": [],
        "circular": False,
        "circular_path": [],
        "unresolved_imports": [],
    }
    
    if file_path in visited:
        result["circular"] = True
        result["circular_path"] = list(visited) + [file_path]
        return result
    
    visited.add(file_path)
    result["chain"].append(file_path)
    
    imports = extract_imports_from_source(file_path)
    
    for imp in imports:
        if imp["type"] == "import":
            module_name = imp["module"]
        elif imp["type"] == "import_from":
            if imp["level"] == 0:
                module_name = imp["module"].split(".")[0] if imp["module"] else None
            else:
                continue
        else:
            continue
        
        if not module_name:
            continue
        
        spec_info = check_module_importable(module_name)
        if not spec_info["spec_found"]:
            result["unresolved_imports"].append({
                "module": module_name,
                "line": imp["line"],
                "reason": spec_info["error"],
            })
    
    return result

def check_protected_file_symbols(file_path: str) -> dict[str, Any]:
    """Check if protected file exports expected symbols."""
    result = {
        "file_path": file_path,
        "module_name": Path(file_path).stem,
        "expected_symbols": [],
        "found_symbols": [],
        "missing_symbols": [],
        "parse_error": None,
    }
    
    expected = EXPECTED_SYMBOLS.get(result["module_name"], [])
    result["expected_symbols"] = expected
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        
        defined_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                defined_names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                defined_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined_names.add(target.id)
        
        result["found_symbols"] = list(defined_names)
        result["missing_symbols"] = [s for s in expected if s not in defined_names]
        
    except SyntaxError as e:
        result["parse_error"] = f"Syntax error at line {e.lineno}: {e.msg}"
    except Exception as e:
        result["parse_error"] = str(e)
    
    return result

def detect_circular_imports_in_hierarchy(hierarchy: list[str]) -> dict[str, Any]:
    """Detect circular imports in signal_analyser hierarchy."""
    result = {
        "hierarchy": hierarchy,
        "circular_chains": [],
        "all_imports": {},
        "analyzed": 0,
    }
    
    visited = {}
    
    for file_path in hierarchy:
        if not os.path.exists(file_path):
            continue
        
        chain_result = check_import_chain(file_path, visited=set())
        result["all_imports"][file_path] = {
            "imports": [i["module"] for i in chain_result.get("chain", [])[:1]],
            "unresolved": chain_result.get("unresolved_imports", []),
        }
        result["analyzed"] += 1
        
        if chain_result["circular"]:
            result["circular_chains"].append(chain_result["circular_path"])
    
    return result

def generate_report(
    sys_path_result: dict[str, Any],
    protected_file_results: list[dict[str, Any]],
    circular_result: dict[str, Any],
) -> dict[str, Any]:
    """Generate structured diagnostic report."""
    report = {
        "probe": SERVICE_NAME,
        "sys_path_check": sys_path_result,
        "protected_files_check": protected_file_results,
        "circular_import_check": circular_result,
        "summary": {
            "sys_path_issues": len(sys_path_result.get("missing_paths", [])),
            "protected_file_issues": sum(1 for r in protected_file_results if r.get("missing_symbols")),
            "circular_chains": len(circular_result.get("circular_chains", [])),
            "severity": "HIGH" if any([
                sys_path_result.get("missing_paths"),
                any(r.get("missing_symbols") for r in protected_file_results),
                circular_result.get("circular_chains"),
            ]) else "LOW",
        },
        "recommendations": [],
    }
    
    if sys_path_result.get("missing_paths"):
        report["recommendations"].append(
            f"Add missing paths to sys.path: {sys_path_result['missing_paths']}"
        )
    
    for pr in protected_file_results:
        if pr.get("missing_symbols"):
            report["recommendations"].append(
                f"{pr['module_name']}: missing expected symbols: {pr['missing_symbols']}"
            )
    
    if circular_result.get("circular_chains"):
        report["recommendations"].append(
            f"Circular import chains detected: {circular_result['circular_chains']}"
        )
    
    return report

def run() -> dict[str, Any]:
    """Main diagnostic run."""
    log.info("Starting smoke import failure root cause probe")
    
    send_heartbeat("running", {"phase": "diagnostic"})
    
    sys_path_result = check_sys_path_entries()
    log.info(f"sys.path check: {len(sys_path_result['missing_paths'])} missing paths")
    
    protected_file_results = []
    for file_path in PROTECTED_FILES:
        if os.path.exists(file_path):
            result = check_protected_file_symbols(file_path)
            protected_file_results.append(result)
            log.info(f"Checked {file_path}: {len(result.get('missing_symbols', []))} missing symbols")
        else:
            log.warning(f"Protected file not found: {file_path}")
            protected_file_results.append({"file_path": file_path, "error": "file not found"})
    
    circular_result = detect_circular_imports_in_hierarchy(SIGNAL_ANALYSER_HIERARCHY)
    log.info(f"Circular import check: {len(circular_result.get('circular_chains', []))} chains")
    
    report = generate_report(sys_path_result, protected_file_results, circular_result)
    
    log.info(f"Diagnostic complete: severity={report['summary']['severity']}")
    
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "status": "complete",
        "ts": report.get("timestamp", ""),
        "meta": {"report": report},
    }])
    
    return report

if __name__ == "__main__":
    from datetime import datetime, timezone
    report = run()
    print(f"severity={report['summary']['severity']}")
    print(f"sys_path_issues={report['summary']['sys_path_issues']}")
    print(f"protected_file_issues={report['summary']['protected_file_issues']}")
    print(f"circular_chains={report['summary']['circular_chains']}")
    if report["recommendations"]:
        print("recommendations:")
        for rec in report["recommendations"]:
            print(f"  - {rec}")
    sys.exit(0)