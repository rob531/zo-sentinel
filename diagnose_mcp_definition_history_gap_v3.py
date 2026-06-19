#!/usr/bin/env python3
"""
Diagnostic utility to investigate why mcp_definition_history table is empty (0 rows).

Per spec section 4: This table is NOT in the normal awaiting-user list;
empty state indicates a pipeline gap.

Queries write_service at :8772 to:
1. Verify mcp_server_registry has entries with definition data
2. Identify which daemon should be writing to mcp_definition_history
3. Check if any error logs indicate failed inserts
4. Output a human-readable report of the gap root cause
"""

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# deps: requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"
TIMEOUT = 15


def ws_query(sql: str, params: Optional[list] = None) -> list[dict[str, Any]]:
    """Query write_service."""
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        import requests
        resp = requests.post(QUERY_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        print(f"Query error: {e}", file=sys.stderr)
        return []


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_history_count() -> int:
    """Get row count from mcp_definition_history."""
    rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    if rows and "cnt" in rows[0]:
        return int(rows[0]["cnt"])
    return -1


def check_registry_count() -> int:
    """Get row count from mcp_server_registry."""
    rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if rows and "cnt" in rows[0]:
        return int(rows[0]["cnt"])
    return -1


def check_registry_has_definition_data() -> dict[str, Any]:
    """Check if mcp_server_registry has entries with definition data."""
    # Check columns that might hold definition data
    sql = """
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN definition IS NOT NULL THEN 1 END) as with_def,
               COUNT(CASE WHEN definition IS NOT NULL AND definition != '' THEN 1 END) as with_def_content
        FROM mcp_server_registry
    """
    rows = ws_query(sql)
    if rows:
        return dict(rows[0])
    return {"total": -1, "with_def": -1, "with_def_content": -1}


def check_history_table_columns() -> list[str]:
    """Get actual columns from mcp_definition_history."""
    sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'mcp_definition_history'
        ORDER BY ordinal_position
    """
    rows = ws_query(sql)
    return [row.get("column_name", "") for row in rows if row.get("column_name")]


def check_service_health() -> list[dict[str, Any]]:
    """Get health records for definition/history daemons."""
    sql = """
        SELECT service, status, last_heartbeat, meta
        FROM service_health
        WHERE service LIKE '%definition%'
           OR service LIKE '%history%'
           OR service LIKE '%mcp%'
        ORDER BY last_heartbeat DESC NULLS LAST
        LIMIT 20
    """
    return ws_query(sql)


def check_all_service_health() -> list[dict[str, Any]]:
    """Get all daemon health records."""
    sql = """
        SELECT service, status, last_heartbeat
        FROM service_health
        ORDER BY service
    """
    return ws_query(sql)


def check_audit_log_errors() -> list[dict[str, Any]]:
    """Check audit_log for errors related to definition/history tables."""
    sql = """
        SELECT event_type, action, outcome, details_json, timestamp
        FROM audit_log
        WHERE (outcome = 'failure' OR outcome LIKE '%error%')
          AND (details_json LIKE '%definition%' OR details_json LIKE '%history%')
        ORDER BY timestamp DESC
        LIMIT 10
    """
    return ws_query(sql)


def check_audit_log_table_writes() -> list[dict[str, Any]]:
    """Check audit_log for any writes to mcp_definition_history."""
    sql = """
        SELECT event_type, action, target_server_id, outcome, timestamp
        FROM audit_log
        WHERE action LIKE '%insert%' OR action LIKE '%write%'
        ORDER BY timestamp DESC
        LIMIT 20
    """
    return ws_query(sql)


def check_schema_definition() -> dict[str, Any]:
    """Get mcp_definition_history table definition from information_schema."""
    sql = """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'mcp_definition_history'
        ORDER BY ordinal_position
    """
    rows = ws_query(sql)
    return {
        "columns": [dict(r) for r in rows],
        "column_count": len(rows)
    }


def check_definition_writer_files() -> dict[str, Any]:
    """Check if definition/history writer files exist."""
    import os
    base = "/home/workspace/zo_sentinel"
    files = {
        "definition_change_history_writer.py": f"{base}/definition_change_history_writer.py",
        "definition_history_writer_daemon.py": f"{base}/definition_history_writer_daemon.py",
        "mcp_definition_history_writer.py": f"{base}/mcp_definition_history_writer.py",
        "mcp_definition_history_writer_daemon.py": f"{base}/mcp_definition_history_writer_daemon.py",
    }
    result = {}
    for name, path in files.items():
        if os.path.exists(path):
            mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
            result[name] = {"exists": True, "modified": mtime.isoformat()}
        else:
            result[name] = {"exists": False}
    return result


def analyze_findings(
    history_count: int,
    registry_count: int,
    registry_def_data: dict[str, Any],
    daemon_health: list[dict[str, Any]],
    audit_errors: list[dict[str, Any]],
    writer_files: dict[str, Any],
) -> dict[str, Any]:
    """Analyze all findings and determine root cause."""
    root_causes = []
    recommendations = []
    severity = "UNKNOWN"

    # Scenario 1: Registry is empty
    if registry_count == 0:
        root_causes.append("mcp_server_registry is empty - no source data to propagate")
        recommendations.append("Populate mcp_server_registry first before history can be written")
        severity = "CRITICAL"
        return {"root_causes": root_causes, "recommendations": recommendations, "severity": severity}

    # Scenario 2: History is empty but registry has data
    if history_count == 0 and registry_count > 0:
        # Check if registry has definition data
        with_def = registry_def_data.get("with_def_content", 0)
        if with_def == 0:
            root_causes.append(
                f"mcp_server_registry has {registry_count} servers but none have definition data"
            )
            recommendations.append("Populate definition field in mcp_server_registry entries")
            severity = "HIGH"
        else:
            # Registry has data, check daemon status
            active_daemons = [
                h for h in daemon_health
                if h.get("status") in ("healthy", "running", "active", "ok")
            ]

            if not daemon_health:
                root_causes.append(
                    "No definition/history daemon health records found in service_health"
                )
                recommendations.append("Start definition_change_history_writer daemon")
                recommendations.append("Check if daemon is registered in service_health")
                severity = "HIGH"
            elif not active_daemons:
                # Daemons exist but all unhealthy
                stale_count = sum(
                    1 for h in daemon_health
                    if h.get("last_heartbeat")
                )
                root_causes.append(
                    f"Definition/history daemons exist but are unhealthy ({stale_count} have heartbeat records)"
                )
                recommendations.append("Check daemon process status with supervisorctl")
                recommendations.append("Review daemon logs for startup or runtime errors")
                severity = "HIGH"
            else:
                # Daemons are healthy but history is empty - logic bug?
                root_causes.append(
                    f"Active daemons detected ({len(active_daemons)}) but history table not populated"
                )
                recommendations.append("Check daemon logic - may be writing to wrong table")
                recommendations.append("Verify daemon is using correct INSERT statements")
                severity = "MEDIUM"

    # Check for audit errors
    if audit_errors:
        error_count = len(audit_errors)
        root_causes.append(f"Found {error_count} error entries in audit_log related to definition/history")
        recommendations.append("Review audit_log errors for specific failure reasons")
        if severity == "UNKNOWN":
            severity = "MEDIUM"

    # Check writer files
    existing_files = [name for name, info in writer_files.items() if info.get("exists")]
    if not existing_files:
        root_causes.append("No definition_history_writer files found in workspace")
        recommendations.append("Build definition_change_history_writer daemon")
        if severity == "UNKNOWN":
            severity = "HIGH"
    else:
        recommendations.append(f"Found writer files: {', '.join(existing_files)}")

    if not root_causes:
        root_causes.append("Could not determine root cause from available data")
        recommendations.append("Run with increased logging verbosity")
        recommendations.append("Check write_service logs directly")

    return {
        "root_causes": root_causes,
        "recommendations": recommendations,
        "severity": severity,
    }


def generate_report(findings: dict[str, Any]) -> str:
    """Generate human-readable diagnostic report."""
    lines = [
        "=" * 70,
        "MCP DEFINITION HISTORY GAP - DIAGNOSTIC REPORT",
        "=" * 70,
        f"Generated: {iso_now()}",
        "",
        "SECTION 1: TABLE STATUS",
        "-" * 40,
        f"  mcp_definition_history rows:    {findings.get('history_count', 'N/A')}",
        f"  mcp_server_registry servers:   {findings.get('registry_count', 'N/A')}",
        "",
        "SECTION 2: REGISTRY DATA QUALITY",
        "-" * 40,
    ]

    reg_def = findings.get("registry_definition_data", {})
    lines.extend([
        f"  Total servers:              {reg_def.get('total', 'N/A')}",
        f"  Servers with definition:     {reg_def.get('with_def', 'N/A')}",
        f"  Servers with definition+:    {reg_def.get('with_def_content', 'N/A')}",
    ])

    lines.extend([
        "",
        "SECTION 3: DAEMON HEALTH",
        "-" * 40,
    ])

    daemon_health = findings.get("daemon_health", [])
    if daemon_health:
        for h in daemon_health[:5]:
            svc = h.get("service", "unknown")
            status = h.get("status", "unknown")
            hb = h.get("last_heartbeat", "no heartbeat")
            lines.append(f"  {svc}: status={status}, last_hb={str(hb)[:19]}")
    else:
        lines.append("  NO DAEMON HEALTH RECORDS FOUND")

    lines.extend([
        "",
        "SECTION 4: AUDIT ERRORS",
        "-" * 40,
    ])

    audit_errors = findings.get("audit_errors", [])
    if audit_errors:
        for e in audit_errors[:5]:
            lines.append(f"  [{e.get('action', '?')}] {e.get('details_json', '')[:60]}...")
    else:
        lines.append("  No audit errors found")

    lines.extend([
        "",
        "SECTION 5: WRITER FILES",
        "-" * 40,
    ])

    writer_files = findings.get("writer_files", {})
    for name, info in writer_files.items():
        status = f"EXISTS ({info.get('modified', '?')[:19]})" if info.get("exists") else "NOT FOUND"
        lines.append(f"  {name}: {status}")

    analysis = findings.get("analysis", {})
    lines.extend([
        "",
        "SECTION 6: ROOT CAUSE ANALYSIS",
        "-" * 40,
        f"  Severity: {analysis.get('severity', 'UNKNOWN')}",
        "",
    ])

    for cause in analysis.get("root_causes", []):
        lines.append(f"  • {cause}")

    lines.extend([
        "",
        "SECTION 7: RECOMMENDATIONS",
        "-" * 40,
    ])

    for rec in analysis.get("recommendations", []):
        lines.append(f"  → {rec}")

    lines.extend([
        "",
        "=" * 70,
    ])

    return "\n".join(lines)


def run() -> int:
    """Run diagnostic and print report. Returns exit code."""
    print("Starting mcp_definition_history gap diagnostic...", file=sys.stderr)
    print(f"Timestamp: {iso_now()}", file=sys.stderr)
    print("", file=sys.stderr)

    # Gather findings
    findings: dict[str, Any] = {
        "timestamp": iso_now(),
        "history_count": check_history_count(),
        "registry_count": check_registry_count(),
        "registry_definition_data": check_registry_has_definition_data(),
        "history_columns": check_history_table_columns(),
        "schema": check_schema_definition(),
        "daemon_health": check_service_health(),
        "all_daemon_health": check_all_service_health(),
        "audit_errors": check_audit_log_errors(),
        "audit_writes": check_audit_log_table_writes(),
        "writer_files": check_definition_writer_files(),
    }

    # Analyze
    findings["analysis"] = analyze_findings(
        findings["history_count"],
        findings["registry_count"],
        findings["registry_definition_data"],
        findings["daemon_health"],
        findings["audit_errors"],
        findings["writer_files"],
    )

    # Generate and print report
    report = generate_report(findings)
    print(report)

    # Return appropriate exit code based on severity
    severity = findings["analysis"].get("severity", "UNKNOWN")
    exit_codes = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4, "UNKNOWN": 0}
    return exit_codes.get(severity, 0)


if __name__ == "__main__":
    sys.exit(run())
