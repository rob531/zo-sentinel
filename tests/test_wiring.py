import os
import sys
import ast
import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Dict, List, Any, Optional, Set

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
WIRING_REPORT_PATH = Path("/home/workspace/zo_sentinel/WIRING_TEST_REPORT.md")

SENTINEL_MODULES = [
    "threat_intel_ingestor",
    "risk_ranker",
    "attestation_engine",
    "signal_analyser",
    "rug_pull_monitor",
    "mcp_scanner",
    "search_api",
    "lookup",
    "trust_synthesiser",
    "db_utils",
    "verdict_taxonomy",
    "env_config",
    "pipeline_health",
    "http_retry",
    "policy_engine_v2",
    "integration_test",
    "text_patterns",
    "assessment_auditor",
    "url_analyser",
    "signal_drift_detector",
    "submission_validator",
    "rate_limiter",
    "policy_engine",
    "error_reporter",
    "startup_checker",
    "dashboard_api",
    "daily_digest",
    "trend_analyser",
    "scoring_cache",
    "compliance_reporter",
    "alert_manager",
    "assessment_scheduler",
    "webhook_dispatcher",
    "mcp_fingerprinter",
    "deduplicator",
    "verdict_explainer",
    "stale_data_cleaner",
    "mcp_profiler",
    "data_validator",
    "mesh_bridge",
    "registry_reconciler",
    "report_formatter",
    "directive_validator",
    "bulk_assess_api",
    "context_injector",
    "comparison_api",
    "notification_hub",
    "pattern_learner",
    "watch",
    "false_positive_tracker",
    "smoke_evolution_agent",
]

DAEMON_MODULES = [
    "threat_intel_ingestor",
    "risk_ranker",
    "attestation_engine",
    "signal_analyser",
    "rug_pull_monitor",
    "mcp_scanner",
    "search_api",
    "trust_synthesiser",
    "pipeline_health",
    "policy_engine_v2",
    "assessment_auditor",
    "signal_drift_detector",
    "policy_engine",
    "error_reporter",
    "dashboard_api",
    "daily_digest",
    "trend_analyser",
    "alert_manager",
    "assessment_scheduler",
    "webhook_dispatcher",
    "stale_data_cleaner",
    "data_validator",
    "mesh_bridge",
    "registry_reconciler",
    "pattern_learner",
    "false_positive_tracker",
    "smoke_evolution_agent",
]

FASTAPI_MODULES = [
    "dashboard_api",
    "bulk_assess_api",
    "comparison_api",
    "search_api",
    "policy_engine_v2",
]

class WiringTestResult:
    def __init__(self, module_name: str):
        self.module_name = module_name
        self.imported = False
        self.import_error: Optional[str] = None
        self.duckdb_check = "SKIP"
        self.duckdb_violations: List[str] = []
        self.run_function = "SKIP"
        self.has_health_route = "SKIP"
        self.has_main_guard = "SKIP"
        self.overall = "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module_name,
            "imported": self.imported,
            "import_error": self.import_error,
            "duckdb_check": self.duckdb_check,
            "duckdb_violations": self.duckdb_violations,
            "run_function": self.run_function,
            "has_health_route": self.has_health_route,
            "has_main_guard": self.has_main_guard,
            "overall": self.overall,
        }

def check_duckdb_violations(module_path: Path) -> List[str]:
    violations = []
    try:
        with open(module_path, "r") as f:
            content = f.read()
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "duckdb" in alias.name.lower():
                        violations.append(f"Import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and "duckdb" in node.module.lower():
                    violations.append(f"ImportFrom: {node.module}")
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name):
                    if node.value.id == "duckdb":
                        violations.append(f"Attribute access: duckdb.{node.attr}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == "duckdb":
                            violations.append(f"Call: duckdb.{node.func.attr}")
    except SyntaxError as e:
        violations.append(f"SyntaxError: {e}")
    except Exception as e:
        violations.append(f"ParseError: {e}")
    return violations

def check_has_run_function(module) -> bool:
    return hasattr(module, "run") and callable(getattr(module, "run", None))

def check_has_main_guard(module_path: Path) -> bool:
    try:
        with open(module_path, "r") as f:
            content = f.read()
        tree = ast.parse(content)
        has_main_guard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                test = node.test
                if isinstance(test, ast.Compare):
                    if isinstance(test.left, ast.Name) and test.left.id == "__name__":
                        for comparator in test.comparators:
                            if isinstance(comparator, ast.Constant):
                                if comparator.value == "__main__":
                                    has_main_guard = True
                                    break
        return has_main_guard
    except:
        return False

def check_health_route(module) -> bool:
    app = getattr(module, "app", None)
    if app is None:
        return False
    if not hasattr(app, "routes"):
        return False
    for route in app.routes:
        if hasattr(route, "path") and route.path == "/health":
            return True
        if hasattr(route, "path") and route.path.endswith("/health"):
            return True
    return False

def load_module(module_name: str) -> Optional[Any]:
    module_path = PROJECT_ROOT / f"{module_name}.py"
    if not module_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return None
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        return None

def test_module(module_name: str, is_daemon: bool = False, is_fastapi: bool = False) -> WiringTestResult:
    result = WiringTestResult(module_name)
    module_path = PROJECT_ROOT / f"{module_name}.py"
    
    if not module_path.exists():
        result.import_error = "Module file not found"
        result.duckdb_check = "NOT_FOUND"
        result.overall = "FAIL"
        return result
    
    violations = check_duckdb_violations(module_path)
    if violations:
        result.duckdb_violations = violations
        result.duckdb_check = "FAIL"
    else:
        result.duckdb_check = "PASS"
    
    module = load_module(module_name)
    if module is None:
        result.import_error = "Failed to import module"
        result.imported = False
        result.overall = "FAIL"
        return result
    
    result.imported = True
    
    if is_daemon:
        if check_has_run_function(module):
            result.run_function = "PASS"
        else:
            result.run_function = "FAIL"
        
        if check_has_main_guard(module_path):
            result.has_main_guard = "PASS"
        else:
            result.has_main_guard = "FAIL"
    
    if is_fastapi:
        if check_health_route(module):
            result.has_health_route = "PASS"
        else:
            result.has_health_route = "FAIL"
    
    if result.duckdb_check == "PASS":
        if not is_daemon and not is_fastapi:
            result.overall = "PASS"
        elif is_daemon and result.run_function == "PASS" and result.has_main_guard == "PASS":
            if is_fastapi:
                if result.has_health_route == "PASS":
                    result.overall = "PASS"
                else:
                    result.overall = "FAIL"
            else:
                result.overall = "PASS"
        elif is_fastapi and result.has_health_route == "PASS":
            if not is_daemon:
                result.overall = "PASS"
            elif result.run_function == "PASS" and result.has_main_guard == "PASS":
                result.overall = "PASS"
            else:
                result.overall = "FAIL"
        else:
            result.overall = "FAIL"
    else:
        result.overall = "FAIL"
    
    return result

def generate_report(results: List[WiringTestResult]) -> str:
    lines = []
    lines.append("# ZO-SENTINEL Wiring Test Report")
    lines.append("")
    lines.append(f"Generated: {Path(__file__).name}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    
    total = len(results)
    passed = sum(1 for r in results if r.overall == "PASS")
    failed = sum(1 for r in results if r.overall == "FAIL")
    skipped = sum(1 for r in results if r.overall == "SKIP")
    
    lines.append(f"- **Total Modules**: {total}")
    lines.append(f"- **Passed**: {passed}")
    lines.append(f"- **Failed**: {failed}")
    lines.append(f"- **Skipped**: {skipped}")
    lines.append("")
    
    all_passed = all(r.overall == "PASS" for r in results)
    if all_passed:
        lines.append("### ✅ ALL TESTS PASSED")
    else:
        lines.append("### ❌ SOME TESTS FAILED")
    lines.append("")
    
    lines.append("## Detailed Results")
    lines.append("")
    lines.append("| Module | Import | DuckDB Check | Run() | Main Guard | /health | Overall |")
    lines.append("|--------|--------|--------------|-------|------------|---------|---------|")
    
    for result in results:
        import_status = "✅" if result.imported else "❌"
        duckdb_status = "✅" if result.duckdb_check == "PASS" else ("❌" if result.duckdb_check == "FAIL" else "➖")
        run_status = "✅" if result.run_function == "PASS" else ("❌" if result.run_function == "FAIL" else "➖")
        guard_status = "✅" if result.has_main_guard == "PASS" else ("❌" if result.has_main_guard == "FAIL" else "➖")
        health_status = "✅" if result.has_health_route == "PASS" else ("❌" if result.has_health_route == "FAIL" else "➖")
        overall_status = "✅" if result.overall == "PASS" else "❌"
        
        lines.append(f"| {result.module_name} | {import_status} | {duckdb_status} | {run_status} | {guard_status} | {health_status} | {overall_status} |")
    
    lines.append("")
    
    failed_results = [r for r in results if r.overall == "FAIL"]
    if failed_results:
        lines.append("## Failure Details")
        lines.append("")
        for result in failed_results:
            lines.append(f"### {result.module_name}")
            lines.append("")
            if result.import_error:
                lines.append(f"- **Import Error**: {result.import_error}")
            if result.duckdb_violations:
                lines.append("- **DuckDB Violations**:")
                for v in result.duckdb_violations:
                    lines.append(f"  - {v}")
            if result.run_function == "FAIL":
                lines.append("- **Missing run() function**")
            if result.has_main_guard == "FAIL":
                lines.append("- **Missing __name__ == '__main__' guard**")
            if result.has_health_route == "FAIL":
                lines.append("- **Missing /health route**")
            lines.append("")
    
    return "\n".join(lines)

def run() -> int:
    print("=" * 60)
    print("ZO-SENTINEL Wiring Test Suite")
    print("=" * 60)
    print()
    
    results: List[WiringTestResult] = []
    has_failures = False
    
    for module_name in SENTINEL_MODULES:
        is_daemon = module_name in DAEMON_MODULES
        is_fastapi = module_name in FASTAPI_MODULES
        test_type = []
        if is_daemon:
            test_type.append("daemon")
        if is_fastapi:
            test_type.append("fastapi")
        test_type_str = ", ".join(test_type) if test_type else "module"
        
        print(f"Testing: {module_name:<30} ({test_type_str})...", end=" ")
        
        result = test_module(module_name, is_daemon=is_daemon, is_fastapi=is_fastapi)
        results.append(result)
        
        if result.overall == "PASS":
            print("✅ PASS")
        else:
            print("❌ FAIL")
            has_failures = True
            if result.duckdb_violations:
                for v in result.duckdb_violations[:3]:
                    print(f"      └─ {v}")
            if result.import_error:
                print(f"      └─ Import error: {result.import_error}")
            if result.run_function == "FAIL":
                print(f"      └─ Missing run() function")
            if result.has_main_guard == "FAIL":
                print(f"      └─ Missing __main__ guard")
            if result.has_health_route == "FAIL":
                print(f"      └─ Missing /health route")
    
    print()
    print("=" * 60)
    print("Generating Report...")
    print("=" * 60)
    
    report = generate_report(results)
    
    with open(WIRING_REPORT_PATH, "w") as f:
        f.write(report)
    
    print(f"Report written to: {WIRING_REPORT_PATH}")
    print()
    
    total = len(results)
    passed = sum(1 for r in results if r.overall == "PASS")
    failed = sum(1 for r in results if r.overall == "FAIL")
    
    print("=" * 60)
    print(f"Results: {passed}/{total} passed, {failed}/{total} failed")
    print("=" * 60)
    
    if has_failures:
        print()
        print("❌ WIRING TESTS FAILED - See report for details")
        return 1
    else:
        print()
        print("✅ ALL WIRING TESTS PASSED")
        return 0

if __name__ == "__main__":
    sys.exit(run())