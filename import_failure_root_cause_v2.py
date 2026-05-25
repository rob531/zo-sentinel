import ast
import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/import_failure_root_cause_v2.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

PROJECT_DIR = Path("/home/workspace/zo_sentinel")
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"

FAILED_FILES = [
    PROJECT_DIR / "registry_api.py",
    PROJECT_DIR / "rug_pull_monitor.py",
    PROJECT_DIR / "signal_analyser.py",
]


def ws_query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Execute read-only query against write_service (evidence only, no writes)."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    response = requests.post(QUERY_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("rows", [])


def check_sys_path_entries() -> dict[str, bool]:
    """Check which sys.path entries exist on disk."""
    entries = {}
    for p in sys.path:
        entries[str(p)] = os.path.isdir(p)
    return entries


def extract_imports_from_source(source: str) -> list[str]:
    """Parse Python source AST and extract all top-level import statements."""
    imports = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        log.warning("AST parse failed: %s", e)
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 0:
                imports.append(("." * node.level) + module)
            else:
                imports.append(module)
    return imports


def resolve_import_path(module_name: str) -> Path | None:
    """Resolve a dotted module name to a file path under PROJECT_DIR."""
    parts = module_name.split(".")
    for depth in range(1, len(parts) + 1):
        candidate = PROJECT_DIR / "/".join(parts[:depth]) + ".py"
        if candidate.exists():
            return candidate
        init = PROJECT_DIR / "/".join(parts[:depth]) / "__init__.py"
        if init.exists():
            return init
    return None


def trace_import_chain(module_name: str, visited: set[str] | None = None) -> dict[str, Any]:
    """Walk import chain for module_name. Returns found/circular/path info."""
    if visited is None:
        visited = set()

    canonical = module_name.split(".")[0]
    if canonical in visited:
        return {
            "module": module_name,
            "circular": True,
            "chain": list(visited) + [module_name],
            "found": True,
            "path": None,
        }

    local_path = resolve_import_path(module_name)
    if local_path:
        return {
            "module": module_name,
            "found": True,
            "path": str(local_path),
            "circular": False,
            "chain": None,
        }

    try:
        spec = importlib.util.find_spec(module_name)
    except Exception:
        spec = None

    if spec is None or spec.origin is None:
        return {
            "module": module_name,
            "found": False,
            "path": None,
            "circular": False,
            "chain": None,
        }

    return {
        "module": module_name,
        "found": True,
        "path": spec.origin,
        "circular": False,
        "chain": None,
    }


def check_module_importable(module_name: str) -> dict[str, Any]:
    """Attempt live import and capture success/failure details."""
    try:
        mod = importlib.import_module(module_name)
        return {"module": module_name, "importable": True, "error": None}
    except ImportError as e:
        return {"module": module_name, "importable": False, "error": str(e)}
    except Exception as e:
        return {"module": module_name, "importable": False, "error": f"Unexpected: {e}"}


def diagnose_file(file_path: Path) -> dict[str, Any]:
    """Full diagnostic for one Python source file."""
    result = {
        "file": str(file_path),
        "exists": file_path.exists(),
        "imports": [],
        "missing_imports": [],
        "circular_dependency_chain": None,
        "import_attempts": [],
        "sys_path_coverage": 0.0,
        "fix_recommendation": None,
    }

    if not file_path.exists():
        result["fix_recommendation"] = "File not found on disk — no diagnostic possible"
        return result

    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as e:
        result["fix_recommendation"] = f"Could not read source file: {e}"
        return result

    result["imports"] = extract_imports_from_source(source)

    sys_path_dirs = {p for p in sys.path if os.path.isdir(p)}
    covered = sum(1 for imp in result["imports"] if resolve_import_path(imp) or importlib.util.find_spec(imp))
    total = len(result["imports"]) or 1
    result["sys_path_coverage"] = round(covered / total, 3)

    missing = []
    circulars = []
    import_attempts = []

    for imp in result["imports"]:
        trace = trace_import_chain(imp)
        live_check = check_module_importable(imp)
        import_attempts.append({"module": imp, **trace, **live_check})

        if not trace["found"]:
            missing.append(imp)
        elif trace.get("circular"):
            circulars.append(trace)

    result["missing_imports"] = missing
    result["import_attempts"] = import_attempts

    if circulars:
        result["circular_dependency_chain"] = circulars[0]["chain"]

    if missing or circulars:
        parts = []
        if missing:
            parts.append(f"Missing modules: {', '.join(missing)}")
        if circulars:
            parts.append(f"Circular chain: {' -> '.join(circulars[0]['chain'])}")
        result["fix_recommendation"] = "; ".join(parts)
    else:
        result["fix_recommendation"] = "No import issues detected in source"

    return result


def build_recommendation(diag: dict[str, Any]) -> str:
    """Translate diagnostic result into a human-readable fix recommendation."""
    if not diag["exists"]:
        return f"REBUILD: {diag['file']} is missing from disk"

    if not diag["missing_imports"] and not diag["circular_dependency_chain"]:
        return "No fix needed — imports resolved"

    missing = diag["missing_imports"]
    circular = diag.get("circular_dependency_chain")

    recs = []
    if missing:
        recs.append(
            f"Install missing modules or add to sys.path: {', '.join(missing)}"
        )
    if circular:
        recs.append(
            f"Break circular import chain: {' -> '.join(circular)}"
        )
    return "; ".join(recs)


def query_recent_smoke_failures() -> list[dict[str, Any]]:
    """Query write_service for recent smoke test failures matching the import pattern."""
    sql = """
        SELECT service, last_heartbeat, meta
        FROM service_health
        WHERE service IN ('registry_api', 'rug_pull_monitor', 'signal_analyser')
        ORDER BY last_heartbeat DESC
        LIMIT 10
    """
    try:
        return ws_query(sql)
    except Exception as e:
        log.warning("write_service query failed (non-critical): %s", e)
        return []


def generate_report(diagnostics: list[dict[str, Any]]) -> str:
    """Build a multi-section text report from all diagnostics."""
    lines = []
    lines.append("=" * 70)
    lines.append("IMPORT FAILURE ROOT CAUSE REPORT")
    lines.append("=" * 70)

    for diag in diagnostics:
        lines.append("")
        lines.append(f"FILE: {diag['file']}")
        lines.append(f"  Exists:          {diag['exists']}")
        lines.append(f"  Imports found:   {len(diag['imports'])}")
        lines.append(f"  Missing imports: {diag['missing_imports']}")
        lines.append(f"  sys.path cover:  {diag['sys_path_coverage']:.1%}")

        if diag["circular_dependency_chain"]:
            lines.append(
                f"  Circular chain:  {' -> '.join(diag['circular_dependency_chain'])}"
            )

        lines.append(f"  Recommendation:  {build_recommendation(diag)}")

        if diag["import_attempts"]:
            lines.append("  Import attempt details:")
            for attempt in diag["import_attempts"]:
                found_str = "FOUND" if attempt["found"] else "MISSING"
                importable_str = "OK" if attempt["importable"] else f"FAIL({attempt.get('error','unknown')})"
                lines.append(
                    f"    - {attempt['module']}: {found_str} | importable={importable_str}"
                )

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def run() -> int:
    """Run the full diagnostic suite."""
    log.info("Starting import_failure_root_cause_v2 diagnostic")

    smoke_evidence = query_recent_smoke_failures()
    log.info("Recent smoke failures from service_health: %d rows", len(smoke_evidence))

    diagnostics = []
    for file_path in FAILED_FILES:
        diag = diagnose_file(file_path)
        diagnostics.append(diag)

    report = generate_report(diagnostics)
    log.info("\n%s", report)

    report_path = Path("/home/workspace/logs/import_failure_root_cause_v2_report.txt")
    report_path.write_text(report, encoding="utf-8")
    log.info("Report written to %s", report_path)

    all_good = all(
        not d["missing_imports"] and not d["circular_dependency_chain"]
        for d in diagnostics
    )
    if all_good:
        log.info("All files passed import diagnostic — no issues found")
        sys.exit(0)
    else:
        issues = sum(
            len(d["missing_imports"]) + (1 if d["circular_dependency_chain"] else 0)
            for d in diagnostics
        )
        log.error("Import issues detected across files: %d total issues", issues)
        sys.exit(1)


if __name__ == "__main__":
    run()