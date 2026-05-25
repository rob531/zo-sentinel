#!/usr/bin/env python3
"""
ZO-SENTINEL Build Diagnostic Report Generator
Diagnoses why certain build scripts failed to produce expected outputs.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/home/workspace/zo_sentinel")


FAILED_SCRIPTS = [
    "build_start_all_sh",
    "build_graphql_schema",
    "build_email_guid_auth",
    "build_mcp_detail_view_ui",
    "build_advanced_filter_api",
    "build_forensic_detail_api",
    "build_manual_override_api",
    "build_compliance_export_service",
    "build_supervisor_auto_updater",
    "build_email_guid_auth_compact",
]


EXPECTED_OUTPUTS = {
    "build_start_all_sh": {
        "expected": ["start_all.sh", "supervisor.conf"],
        "type": "shell_script",
        "description": "Startup script for all services",
    },
    "build_graphql_schema": {
        "expected": ["graphql_schema.py", "schema.graphql"],
        "type": "api_schema",
        "description": "GraphQL schema for MCP server queries",
    },
    "build_email_guid_auth": {
        "expected": ["email_auth.py", "guid_service.py"],
        "type": "auth_module",
        "description": "Email/GUID authentication system",
    },
    "build_mcp_detail_view_ui": {
        "expected": ["mcp_detail_view.html", "mcp_detail_view.js"],
        "type": "frontend_ui",
        "description": "MCP server detail view UI",
    },
    "build_advanced_filter_api": {
        "expected": ["advanced_filter_api.py"],
        "type": "api_endpoint",
        "description": "Advanced filtering for threat intelligence",
    },
    "build_forensic_detail_api": {
        "expected": ["forensic_detail_api.py"],
        "type": "api_endpoint",
        "description": "Forensic analysis detail API",
    },
    "build_manual_override_api": {
        "expected": ["manual_override_api.py"],
        "type": "api_endpoint",
        "description": "Manual override controls API",
    },
    "build_compliance_export_service": {
        "expected": ["compliance_export.py", "report_generator.py"],
        "type": "service",
        "description": "Compliance reporting and export service",
    },
    "build_supervisor_auto_updater": {
        "expected": ["supervisor_updater.py"],
        "type": "service",
        "description": "Auto-update service for supervisor configs",
    },
    "build_email_guid_auth_compact": {
        "expected": ["email_auth_compact.py"],
        "type": "auth_module",
        "description": "Compact version of email/GUID auth",
    },
}


def check_file_exists(filepath: Path) -> dict[str, Any]:
    """Check if a file exists and get its metadata."""
    if filepath.exists():
        stat = filepath.stat()
        return {
            "exists": True,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "permissions": oct(stat.st_mode)[-3:],
        }
    return {"exists": False, "size_bytes": 0, "modified": None, "permissions": None}


def check_script_exists(script_name: str) -> dict[str, Any]:
    """Check if a build script exists."""
    scripts_dir = PROJECT_ROOT / "scripts" / "build"
    script_path = scripts_dir / script_name
    return check_file_exists(script_path)


def check_outputs_exist(script_name: str) -> dict[str, Any]:
    """Check if expected output files exist."""
    info = EXPECTED_OUTPUTS.get(script_name, {})
    outputs = {}
    
    for expected_file in info.get("expected", []):
        output_path = PROJECT_ROOT / expected_file
        outputs[expected_file] = check_file_exists(output_path)
    
    return outputs


def get_related_source_files(script_name: str) -> list[str]:
    """Identify related source files that might be dependencies."""
    dependencies = {
        "build_start_all_sh": ["config.py", "service_manager.py"],
        "build_graphql_schema": ["models.py", "api.py"],
        "build_email_guid_auth": ["auth.py", "models.py"],
        "build_mcp_detail_view_ui": ["models.py", "api.py"],
        "build_advanced_filter_api": ["threat_intel_ingestor.py", "models.py"],
        "build_forensic_detail_api": ["models.py", "db_service.py"],
        "build_manual_override_api": ["control_service.py"],
        "build_compliance_export_service": ["models.py", "db_service.py"],
        "build_supervisor_auto_updater": ["config.py"],
        "build_email_guid_auth_compact": ["auth.py"],
    }
    return dependencies.get(script_name, [])


def diagnose_script(script_name: str) -> dict[str, Any]:
    """Diagnose a single failed build script."""
    diagnosis = {
        "script_name": script_name,
        "script_info": EXPECTED_OUTPUTS.get(script_name, {}),
        "script_file": check_script_exists(script_name),
        "expected_outputs": check_outputs_exist(script_name),
        "dependencies": {},
        "issues": [],
        "recommendations": [],
        "status": "unknown",
    }
    
    script_exists = diagnosis["script_file"]["exists"]
    if not script_exists:
        diagnosis["issues"].append("Build script file does not exist")
        diagnosis["recommendations"].append("Create the build script in scripts/build/")
    
    output_statuses = [out["exists"] for out in diagnosis["expected_outputs"].values()]
    if output_statuses:
        if not any(output_statuses):
            diagnosis["issues"].append("None of the expected output files exist")
            diagnosis["status"] = "missing_outputs"
        elif not all(output_statuses):
            diagnosis["issues"].append("Some expected output files are missing")
            diagnosis["status"] = "partial_outputs"
        else:
            diagnosis["status"] = "outputs_exist"
    
    if script_exists and diagnosis["status"] in ["missing_outputs", "partial_outputs"]:
        diagnosis["issues"].append("Script exists but did not generate expected outputs")
        diagnosis["recommendations"].append("Verify script execution completed successfully")
        diagnosis["recommendations"].append("Check for runtime errors in script execution")
    
    related_files = get_related_source_files(script_name)
    for rel_file in related_files:
        rel_path = PROJECT_ROOT / rel_file
        diagnosis["dependencies"][rel_file] = check_file_exists(rel_path)
        
        if not check_file_exists(rel_path)["exists"]:
            diagnosis["issues"].append(f"Dependency file missing: {rel_file}")
            diagnosis["recommendations"].append(f"Create or restore missing dependency: {rel_file}")
    
    if not diagnosis["issues"]:
        diagnosis["status"] = "ok"
        diagnosis["issues"].append("No issues detected")
    
    return diagnosis


def generate_diagnostic_report() -> dict[str, Any]:
    """Generate comprehensive diagnostic report for all failed scripts."""
    report = {
        "report_metadata": {
            "generated_at": datetime.now().isoformat(),
            "project_root": str(PROJECT_ROOT),
            "total_scripts_analyzed": len(FAILED_SCRIPTS),
        },
        "summary": {
            "scripts_missing": 0,
            "scripts_with_missing_outputs": 0,
            "scripts_with_missing_deps": 0,
            "scripts_ok": 0,
        },
        "diagnostics": [],
        "global_issues": [],
        "global_recommendations": [],
    }
    
    all_dependencies = {}
    
    for script_name in FAILED_SCRIPTS:
        diag = diagnose_script(script_name)
        report["diagnostics"].append(diag)
        
        if not diag["script_file"]["exists"]:
            report["summary"]["scripts_missing"] += 1
        elif diag["status"] in ["missing_outputs", "partial_outputs"]:
            report["summary"]["scripts_with_missing_outputs"] += 1
        elif diag["status"] == "ok":
            report["summary"]["scripts_ok"] += 1
        
        if any(not dep["exists"] for dep in diag["dependencies"].values()):
            report["summary"]["scripts_with_missing_deps"] += 1
        
        for dep_file, dep_info in diag["dependencies"].items():
            if not dep_info["exists"]:
                if dep_file not in all_dependencies:
                    all_dependencies[dep_file] = []
                all_dependencies[dep_file].append(script_name)
    
    if all_dependencies:
        report["global_issues"].append("Multiple scripts depend on missing files")
        for dep, scripts in all_dependencies.items():
            report["global_issues"].append(f"  {dep} is needed by: {', '.join(scripts)}")
        
        report["global_recommendations"].append(
            "Prioritize creating files with most dependencies first"
        )
    
    if report["summary"]["scripts_missing"] > 3:
        report["global_recommendations"].append(
            "Consider rebuilding the build script infrastructure"
        )
    
    report["summary"]["total_issues"] = len(report["global_issues"])
    
    return report


def main():
    """Main entry point."""
    report = generate_diagnostic_report()
    
    output_path = PROJECT_ROOT / "build_diagnostic_report.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(json.dumps(report, indent=2))
    
    return report


if __name__ == "__main__":
    main()