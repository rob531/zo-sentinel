import os
import sys
import json
import requests
from datetime import datetime

SERVICE_NAME = "diagnose_failed_build_modules"
PROJECT_ROOT = "/home/workspace/zo_sentinel"
BUILD_SCRIPTS_DIR = PROJECT_ROOT
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

FAILED_MODULES = [
    "build_start_all_sh",
    "build_graphql_schema",
    "build_email_guid_auth",
    "build_mcp_detail_view_ui",
    "build_advanced_filter_api",
    "build_forensic_detail_api",
    "build_manual_override_api",
    "build_compliance_export_service",
    "build_supervisor_auto_updater",
]

PROTECTED_FILES = [
    "registry_api.py",
    "signal_analyser.py",
    "rug_pull_monitor.py",
    "trust_synthesiser.py",
    "trust_synthesiser_v2.py",
    "trust_synthesiser_v3.py",
    "attestation_engine.py",
    "mcp_scanner.py",
    "threat_intel_ingestor.py",
    "risk_ranker.py",
    "policy_engine.py",
    "policy_engine_v2.py",
    "email_guid_auth.py",
    "advanced_filter_api.py",
    "forensic_detail_api.py",
    "manual_override_api.py",
    "compliance_export_service.py",
    "supervisor_auto_updater.py",
]

ALREADY_BUILT_MAPPING = {
    "build_start_all_sh": ["start_all.sh"],
    "build_graphql_schema": ["graphql_schema_builder.py", "graphql_schema_builder_v2.py"],
    "build_email_guid_auth": ["email_guid_auth.py", "email_guid_auth_v2.py"],
    "build_mcp_detail_view_ui": ["mcp_detail_view_ui.html", "mcp_detail_view_ui_v2.html", "mcp_detail_view.html"],
    "build_advanced_filter_api": ["advanced_filter_api.py"],
    "build_forensic_detail_api": ["forensic_detail_api.py"],
    "build_manual_override_api": ["manual_override_api.py"],
    "build_compliance_export_service": ["compliance_export_service.py", "compliance_export_service_v2.py"],
    "build_supervisor_auto_updater": ["supervisor_auto_updater.py"],
}


def log(msg):
    ts = datetime.utcnow().isoformat()
    print(f"[{ts}] {msg}", flush=True)


def ws_query(sql):
    try:
        r = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"QUERY_ERROR: {e}")
        return {"rows": [], "count": 0}


def ws_execute(sql):
    try:
        r = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"EXECUTE_ERROR: {e}")
        return {"ok": False}


def ws_write(table, rows):
    try:
        r = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log(f"WRITE_ERROR: {e}")
        return {"ok": False}


def check_file_exists(path):
    return os.path.exists(path) and os.path.isfile(path)


def check_build_script_exists(module_name):
    script_path = os.path.join(BUILD_SCRIPTS_DIR, f"{module_name}.py")
    return check_file_exists(script_path)


def check_output_files_exist(module_name):
    expected_outputs = ALREADY_BUILT_MAPPING.get(module_name, [])
    existing = []
    missing = []
    for f in expected_outputs:
        full_path = os.path.join(PROJECT_ROOT, f)
        if check_file_exists(full_path):
            existing.append(f)
        else:
            missing.append(f)
    return existing, missing


def is_protected(module_name):
    expected = ALREADY_BUILT_MAPPING.get(module_name, [])
    for f in expected:
        if f in PROTECTED_FILES:
            return True
    return False


def get_build_attempt_history(module_name):
    sql = f"SELECT * FROM audit_log WHERE detail LIKE '%{module_name}%' AND event_type IN ('build_attempt', 'build_failure', 'build_success') ORDER BY created_at DESC LIMIT 5"
    result = ws_query(sql)
    return result.get("rows", [])


def get_smoke_test_history(module_name):
    sql = f"SELECT * FROM audit_log WHERE detail LIKE '%{module_name}%' AND event_type = 'smoke_test' ORDER BY created_at DESC LIMIT 3"
    result = ws_query(sql)
    return result.get("rows", [])


def read_build_script_header(module_name):
    path = os.path.join(BUILD_SCRIPTS_DIR, f"{module_name}.py")
    try:
        with open(path, 'r') as f:
            lines = f.readlines()[:50]
            return ''.join(lines)
    except:
        return ""


def diagnose_module(module_name):
    result = {
        "module": module_name,
        "script_exists": check_build_script_exists(module_name),
        "already_built": [],
        "missing_outputs": [],
        "is_protected": is_protected(module_name),
        "build_history": [],
        "smoke_history": [],
        "recommendation": None,
        "priority": None,
    }
    
    existing, missing = check_output_files_exist(module_name)
    result["already_built"] = existing
    result["missing_outputs"] = missing
    
    result["build_history"] = get_build_attempt_history(module_name)
    result["smoke_history"] = get_smoke_test_history(module_name)
    
    if result["is_protected"]:
        result["recommendation"] = "SKIP - Output file is protected"
        result["priority"] = 0
    elif existing:
        result["recommendation"] = "SKIP - Output already built successfully"
        result["priority"] = 0
    elif not result["script_exists"]:
        result["recommendation"] = "SKIP - Build script missing"
        result["priority"] = 0
    elif len(missing) == len(ALREADY_BUILT_MAPPING.get(module_name, [])):
        result["recommendation"] = "RETRY - All outputs missing, fresh build needed"
        result["priority"] = 1
    else:
        result["recommendation"] = "RETRY_PARTIAL - Some outputs exist but incomplete"
        result["priority"] = 2
    
    return result


def analyze_discrimination_pattern(results):
    modules_with_history = []
    modules_no_history = []
    
    for r in results:
        if r["build_history"] or r["smoke_history"]:
            modules_with_history.append(r["module"])
        else:
            modules_no_history.append(r["module"])
    
    return modules_with_history, modules_no_history


def generate_diagnostic_report(results):
    report = []
    report.append("=" * 80)
    report.append("ZO-SENTINEL BUILD FAILURE DIAGNOSTIC REPORT")
    report.append(f"Generated: {datetime.utcnow().isoformat()}")
    report.append("=" * 80)
    report.append("")
    
    report.append("MODULE ANALYSIS:")
    report.append("-" * 40)
    
    for r in sorted(results, key=lambda x: x["priority"] if x["priority"] else 99):
        report.append(f"\nModule: {r['module']}")
        report.append(f"  Script exists: {r['script_exists']}")
        report.append(f"  Already built: {r['already_built'] or 'None'}")
        report.append(f"  Missing outputs: {r['missing_outputs'] or 'None'}")
        report.append(f"  Protected: {r['is_protected']}")
        report.append(f"  Build history count: {len(r['build_history'])}")
        report.append(f"  Smoke history count: {len(r['smoke_history'])}")
        report.append(f"  Recommendation: {r['recommendation']}")
        report.append(f"  Priority: {r['priority']}")
    
    report.append("")
    report.append("=" * 80)
    report.append("PRIORITIZED ACTIONS:")
    report.append("-" * 40)
    
    retry_list = [r for r in results if r["priority"] > 0]
    skip_list = [r for r in results if r["priority"] == 0]
    
    report.append(f"\nModules to REBUILD ({len(retry_list)}):")
    for r in sorted(retry_list, key=lambda x: x["priority"]):
        report.append(f"  [{r['priority']}] {r['module']}")
        report.append(f"      -> {r['recommendation']}")
        if r["missing_outputs"]:
            report.append(f"      -> Expected: {r['missing_outputs']}")
    
    report.append(f"\nModules to SKIP ({len(skip_list)}):")
    for r in skip_list:
        report.append(f"  [0] {r['module']}")
        report.append(f"      -> {r['recommendation']}")
    
    modules_with_history, modules_no_history = analyze_discrimination_pattern(results)
    report.append("")
    report.append("=" * 80)
    report.append("BUILD HISTORY ANALYSIS:")
    report.append("-" * 40)
    report.append(f"  Modules with build history: {len(modules_with_history)}")
    report.append(f"    {modules_with_history}")
    report.append(f"  Modules with NO build history: {len(modules_no_history)}")
    report.append(f"    {modules_no_history}")
    
    return "\n".join(report)


def write_diagnostic_to_db(results):
    for r in results:
        diag = {
            "module": r["module"],
            "script_exists": r["script_exists"],
            "already_built": ",".join(r["already_built"]) if r["already_built"] else None,
            "missing_outputs": ",".join(r["missing_outputs"]) if r["missing_outputs"] else None,
            "is_protected": r["is_protected"],
            "build_history_count": len(r["build_history"]),
            "smoke_history_count": len(r["smoke_history"]),
            "recommendation": r["recommendation"],
            "priority": r["priority"],
            "diagnosed_at": datetime.utcnow().isoformat(),
        }
        ws_write("diagnostic_build_failures", diag)


def main():
    log("Starting failed build modules diagnostic...")
    
    results = []
    for module in FAILED_MODULES:
        log(f"Analyzing {module}...")
        r = diagnose_module(module)
        results.append(r)
    
    report = generate_diagnostic_report(results)
    print(report)
    
    write_diagnostic_to_db(results)
    
    retry_count = len([r for r in results if r["priority"] > 0])
    log(f"\nDiagnostic complete: {retry_count} modules need rebuild, {len(results) - retry_count} should be skipped")
    
    if retry_count > 0:
        log("\nREBUILD CANDIDATES:")
        for r in sorted(results, key=lambda x: x["priority"] if x["priority"] else 99):
            if r["priority"] > 0:
                log(f"  - {r['module']}: {r['recommendation']}")


if __name__ == "__main__":
    main()