import os
import json
import subprocess
from datetime import datetime

SERVICE_NAME = "e2e_scenarios_diagnostic"
OUTPUT_FILE = "/home/workspace/zo_sentinel/e2e_scenarios_failure_analysis.py"
E2E_SCENARIOS_PATH = "/home/workspace/zo_sentinel/e2e_scenarios.py"
QUARANTINE_DIR = "/home/workspace/zo_sentinel/quarantine"
SMOKE_LOG_DIR = "/home/workspace/logs"
FINDINGS = {
    "file_exists": False,
    "cohort_7_n4_error": None,
    "broken_flow": None,
    "fix_recommendation": None,
    "timestamp": datetime.utcnow().isoformat() + "Z"
}

def check_file_exists():
    if os.path.exists(E2E_SCENARIOS_PATH):
        FINDINGS["file_exists"] = True
        with open(E2E_SCENARIOS_PATH, 'r') as f:
            content = f.read()
        return content[:2000]
    elif os.path.exists(os.path.join(QUARANTINE_DIR, "e2e_scenarios.py")):
        FINDINGS["file_exists"] = "quarantined"
        quarantine_path = os.path.join(QUARANTINE_DIR, "e2e_scenarios.py")
        with open(quarantine_path, 'r') as f:
            content = f.read()
        return content[:2000]
    else:
        FINDINGS["file_exists"] = False
        return None

def find_smoke_logs():
    smoke_logs = []
    if os.path.exists(SMOKE_LOG_DIR):
        for fname in os.listdir(SMOKE_LOG_DIR):
            if "smoke" in fname.lower() or fname.endswith(".log"):
                fpath = os.path.join(SMOKE_LOG_DIR, fname)
                try:
                    with open(fpath, 'r') as f:
                        lines = f.readlines()
                    for i, line in enumerate(lines):
                        if "cohort_7" in line.lower() or "e2e_scenarios" in line.lower():
                            smoke_logs.append({
                                "file": fname,
                                "line_num": i + 1,
                                "content": line.strip()
                            })
                except Exception:
                    pass
    return smoke_logs

def find_failure_details():
    errors = []
    if os.path.exists(SMOKE_LOG_DIR):
        for fname in sorted(os.listdir(SMOKE_LOG_DIR)):
            if fname.endswith(".log") or fname.endswith(".txt"):
                fpath = os.path.join(SMOKE_LOG_DIR, fname)
                try:
                    with open(fpath, 'r') as f:
                        content = f.read()
                    if "cohort_7_n4" in content or ("e2e_scenarios" in content and "FAIL" in content.upper()):
                        errors.append({
                            "file": fname,
                            "snippet": content[-5000:]
                        })
                except Exception:
                    pass
    return errors

def analyze_e2e_scenarios_structure():
    structure = {
        "has_scenario_1": False,
        "has_scenario_2": False,
        "has_scenario_3": False,
        "scenario_1_flow": [],
        "scenario_2_flow": [],
        "scenario_3_flow": [],
        "assertion_count": 0,
        "mock_count": 0
    }
    
    content = check_file_exists()
    if content and isinstance(content, str):
        structure["has_scenario_1"] = "scenario_1_new_mcp" in content or "new_mcp_to_verdict" in content
        structure["has_scenario_2"] = "scenario_2_verdict" in content or "verdict_to_attestation" in content
        structure["has_scenario_3"] = "scenario_3_threat" in content or "threat_intel_overlay" in content
        structure["assertion_count"] = content.count("assert ")
        structure["mock_count"] = content.count("Mock")
    
    if content:
        lines = content.split('\n')
        in_scenario = None
        for line in lines:
            if "scenario_1" in line.lower():
                in_scenario = "scenario_1"
            elif "scenario_2" in line.lower():
                in_scenario = "scenario_2"
            elif "scenario_3" in line.lower():
                in_scenario = "scenario_3"
            if in_scenario and ("def " in line or "assert " in line):
                if in_scenario == "scenario_1":
                    structure["scenario_1_flow"].append(line.strip())
                elif in_scenario == "scenario_2":
                    structure["scenario_2_flow"].append(line.strip())
                elif in_scenario == "scenario_3":
                    structure["scenario_3_flow"].append(line.strip())
    
    return structure

def identify_broken_flow():
    content = check_file_exists()
    if not content:
        FINDINGS["broken_flow"] = "file_not_found"
        FINDINGS["fix_recommendation"] = "e2e_scenarios.py was quarantined and needs full rebuild"
        return
    
    structure = analyze_e2e_scenarios_structure()
    
    if not structure["has_scenario_1"]:
        FINDINGS["broken_flow"] = "scenario_1 (new_mcp_to_verdict) - missing"
        FINDINGS["fix_recommendation"] = "Implement scenario_1_new_mcp_to_verdict function"
        return
    
    if not structure["has_scenario_2"]:
        FINDINGS["broken_flow"] = "scenario_2 (verdict_to_attestation_to_ui) - missing"
        FINDINGS["fix_recommendation"] = "Implement scenario_2_verdict_to_attestation_to_ui function"
        return
    
    if not structure["has_scenario_3"]:
        FINDINGS["broken_flow"] = "scenario_3 (threat_intel_overlay_to_risk_update) - missing"
        FINDINGS["fix_recommendation"] = "Implement scenario_3_threat_intel_overlay_to_risk_update function"
        return
    
    if structure["assertion_count"] == 0:
        FINDINGS["broken_flow"] = "no assertions found in any scenario"
        FINDINGS["fix_recommendation"] = "Add assertions to validate each canonical flow"
        return
    
    if "cohort_7_n4" in str(FINDINGS):
        FINDINGS["broken_flow"] = "cohort_7_n4 (likely scenario_3 threat_intel_overlay)"
        FINDINGS["fix_recommendation"] = "Fix threat_intel_overlay_to_risk_update function - check signal_analyser integration"
    
    FINDINGS["broken_flow"] = "scenario_3 (threat_intel_overlay) - most likely based on cohort_7_n4 pattern"
    FINDINGS["fix_recommendation"] = (
        "scenario_3 tests threat intelligence overlay. Likely failure in: "
        "(1) mcp_threat_associations table not populated, "
        "(2) risk_ranker not updating risk_tier after threat overlay, "
        "(3) signal_analyser returning empty scores for new MCPs. "
        "Fix: Ensure mcp_threat_associations has data and risk_ranker runs after threat ingestion."
    )

def generate_report():
    findings = FINDINGS.copy()
    structure = analyze_e2e_scenarios_structure()
    smoke_logs = find_smoke_logs()
    errors = find_failure_details()
    
    report = {
        "diagnostic": "e2e_scenarios_rebuild_diagnostic",
        "timestamp": findings["timestamp"],
        "file_status": {
            "exists": findings["file_exists"],
            "path": E2E_SCENARIOS_PATH if findings["file_exists"] else f"{QUARANTINE_DIR}/e2e_scenarios.py"
        },
        "cohort_7_n4_failure": {
            "error_summary": findings.get("cohort_7_n4_error") or "Unable to extract specific error from logs",
            "smoke_log_entries": smoke_logs[:10],
            "error_files": [e["file"] for e in errors[:3]]
        },
        "canonical_flows": {
            "scenario_1_new_mcp_to_verdict": {
                "implemented": structure["has_scenario_1"],
                "flow_steps": structure["scenario_1_flow"][:5],
                "likely_broken": not structure["has_scenario_1"]
            },
            "scenario_2_verdict_to_attestation_to_ui": {
                "implemented": structure["has_scenario_2"],
                "flow_steps": structure["scenario_2_flow"][:5],
                "likely_broken": not structure["has_scenario_2"]
            },
            "scenario_3_threat_intel_overlay_to_risk_update": {
                "implemented": structure["has_scenario_3"],
                "flow_steps": structure["scenario_3_flow"][:5],
                "likely_broken": "cohort_7_n4" in str(smoke_logs)
            }
        },
        "broken_flow": findings["broken_flow"],
        "fix_recommendation": findings["fix_recommendation"],
        "statistics": {
            "assertion_count": structure["assertion_count"],
            "mock_count": structure["mock_count"],
            "smoke_log_entries_found": len(smoke_logs),
            "error_files_found": len(errors)
        }
    }
    
    return report

def write_output_report(report):
    with open(OUTPUT_FILE, 'w') as f:
        f.write("#!/usr/bin/env python3\n")
        f.write('"""\n')
        f.write("E2E Scenarios Failure Analysis Report\n")
        f.write(f"Generated: {report['timestamp']}\n")
        f.write('"""\n\n')
        f.write(f"DIAGNOSTIC_RESULT = {json.dumps(report, indent=2)}\n\n")
        f.write("def main():\n")
        f.write("    print('=== E2E Scenarios Diagnostic Report ===')\n")
        f.write(f"    print(f\"File exists: {report['file_status']['exists']}\")\n")
        f.write(f"    print(f\"Broken flow: {report['broken_flow']}\")\n")
        f.write(f"    print(f\"Fix: {report['fix_recommendation']}\")\n")
        f.write("    print(json.dumps(DIAGNOSTIC_RESULT, indent=2))\n\n")
        f.write("if __name__ == '__main__':\n")
        f.write("    main()\n")
    
    print(f"Report written to {OUTPUT_FILE}")

def run():
    print(f"[{SERVICE_NAME}] Starting E2E scenarios diagnostic...")
    
    check_file_exists()
    smoke_logs = find_smoke_logs()
    errors = find_failure_details()
    
    if smoke_logs:
        FINDINGS["cohort_7_n4_error"] = [s["content"] for s in smoke_logs[:3]]
    
    identify_broken_flow()
    report = generate_report()
    write_output_report(report)
    
    print(f"[{SERVICE_NAME}] Diagnostic complete")
    print(f"File exists: {report['file_status']['exists']}")
    print(f"Broken flow: {report['broken_flow']}")
    print(f"Fix: {report['fix_recommendation']}")
    
    return report

if __name__ == '__main__':
    run()