#!/usr/bin/env python3
"""
Diagnostic utility to investigate why mcp_definition_history table is empty (0 rows).

Checks:
1. Table schema via information_schema.columns
2. Writer's expected columns vs actual table columns
3. audit_log for recent writes to this table
4. mcp_server_registry as data source availability
5. Service health for definition/history daemons

This is a READ-ONLY diagnostic -- no writes, no rebuild proposals.
Per gaps map guidance: diagnostic-only file.
"""
import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# deps: requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
MAX_RETRIES = 3
TIMEOUT = 10


def query_ws(sql: str, params: Optional[list] = None) -> dict:
    """Query write_service with retry."""
    import requests
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(QUERY_URL, json=payload, timeout=TIMEOUT)
            if resp.status_code >= 500:
                import time
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                return {"error": str(e), "rows": []}
            import time
            time.sleep(2 ** attempt)
    return {"error": "exhausted retries", "rows": []}


def run_diagnostics() -> dict:
    """
    Run all diagnostic checks against mcp_definition_history.
    Returns findings dict for reporting.
    """
    findings = {
        "diagnostic": "investigate_definition_history_gap",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "root_causes": [],
        "actionable_findings": []
    }

    # CHECK 1: Table schema via information_schema.columns
    schema_result = query_ws(
        "SELECT table_name, column_name, data_type, ordinal_position "
        "FROM information_schema.columns "
        "WHERE table_name = 'mcp_definition_history' "
        "ORDER BY ordinal_position"
    )
    
    actual_columns: list[str] = []
    if schema_result.get("rows"):
        for row in schema_result["rows"]:
            col_name = row.get("column_name")
            if col_name:
                actual_columns.append(col_name)
    
    findings["checks"].append({
        "name": "table_schema",
        "actual_columns": actual_columns,
        "column_count": len(actual_columns),
        "schema_result": schema_result.get("rows", [])
    })

    # CHECK 2: Row count
    count_result = query_ws("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    row_count = 0
    if count_result.get("rows"):
        first_row = count_result["rows"][0]
        cnt_val = first_row.get("cnt") if isinstance(first_row, dict) else first_row[0]
        row_count = int(cnt_val) if cnt_val is not None else 0
    
    findings["checks"].append({
        "name": "row_count",
        "count": row_count
    })

    # CHECK 3: Sample rows to verify column mapping
    sample_result = query_ws("SELECT * FROM mcp_definition_history LIMIT 3")
    findings["checks"].append({
        "name": "sample_rows",
        "sample": sample_result.get("rows", [])
    })

    # CHECK 4: Writer expected columns vs actual
    # From definition_change_history_writer.py _build_history_row method
    writer_expected_columns = [
        "mcp_id", "changed_at", "change_type", "tool_name",
        "before_schema", "after_schema", "diff_summary"
    ]
    
    # Actual table columns from DB_SCHEMA.md
    db_schema_columns = ["id", "server_id", "snapshot_hash", "captured_at"]
    
    missing_in_table: list[str] = []
    for col in writer_expected_columns:
        if col not in actual_columns and col not in db_schema_columns:
            missing_in_table.append(col)
    
    extra_in_table: list[str] = []
    for col in db_schema_columns:
        if col not in writer_expected_columns:
            extra_in_table.append(col)
    
    findings["checks"].append({
        "name": "column_mismatch_analysis",
        "writer_expected": writer_expected_columns,
        "actual_table_columns": db_schema_columns,
        "missing_in_table": missing_in_table,
        "extra_in_table": extra_in_table,
        "mismatch_detected": len(missing_in_table) > 0 or len(extra_in_table) > 0
    })

    # CHECK 5: mcp_server_registry as data source
    registry_result = query_ws(
        "SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE tool_schema IS NOT NULL"
    )
    registry_count = 0
    if registry_result.get("rows"):
        first_row = registry_result["rows"][0]
        cnt_val = first_row.get("cnt") if isinstance(first_row, dict) else first_row[0]
        registry_count = int(cnt_val) if cnt_val is not None else 0
    
    findings["checks"].append({
        "name": "data_source_availability",
        "servers_with_tool_schema": registry_count
    })

    # CHECK 6: audit_log for writes to mcp_definition_history
    audit_result = query_ws(
        "SELECT event_type, actor, target_server_id, action, outcome, details_json, timestamp "
        "FROM audit_log "
        "WHERE details_json LIKE '%mcp_definition_history%' "
        "ORDER BY timestamp DESC "
        "LIMIT 10"
    )
    findings["checks"].append({
        "name": "audit_log_writes",
        "write_count": len(audit_result.get("rows", [])),
        "writes": audit_result.get("rows", [])
    })

    # CHECK 7: audit_log for failures related to definition/history
    failure_result = query_ws(
        "SELECT event_type, action, outcome, details_json, timestamp "
        "FROM audit_log "
        "WHERE outcome = 'failure' "
        "AND (details_json LIKE '%definition%' OR details_json LIKE '%history%') "
        "ORDER BY timestamp DESC "
        "LIMIT 5"
    )
    findings["checks"].append({
        "name": "audit_log_failures",
        "failure_count": len(failure_result.get("rows", [])),
        "failures": failure_result.get("rows", [])
    })

    # CHECK 8: Service health for definition/history daemons
    health_result = query_ws(
        "SELECT service, status, last_heartbeat, meta "
        "FROM service_health "
        "WHERE service LIKE '%definition%' "
        "OR service LIKE '%history%' "
        "OR service LIKE '%change%' "
        "ORDER BY last_heartbeat DESC"
    )
    findings["checks"].append({
        "name": "daemon_health",
        "daemons": health_result.get("rows", [])
    })

    # CHECK 9: Check if definition_change_history_writer.py file exists
    import os
    writer_path = "/home/workspace/zo_sentinel/definition_change_history_writer.py"
    findings["checks"].append({
        "name": "writer_file",
        "path": writer_path,
        "exists": os.path.exists(writer_path)
    })
    
    if os.path.exists(writer_path):
        with open(writer_path) as f:
            content = f.read()
        
        # Find table reference
        table_refs = []
        for line in content.split("\n"):
            if '"table"' in line and 'mcp_definition_history' in line:
                table_refs.append(line.strip())
        
        # Find column names in _build_history_row
        history_row_method = []
        in_method = False
        for line in content.split("\n"):
            if "_build_history_row" in line:
                in_method = True
            if in_method:
                history_row_method.append(line)
                if in_method and line.strip() and not line.strip().startswith('#') and '"""' in line:
                    in_method = False
        
        findings["checks"][-1]["table_references"] = table_refs[:5]
        findings["checks"][-1]["history_row_method_snippet"] = history_row_method[:20]

    # DETERMINE ROOT CAUSES
    if row_count == 0:
        if len(missing_in_table) > 0:
            findings["root_causes"].append({
                "cause": "column_name_mismatch",
                "severity": "CRITICAL",
                "detail": f"Writer writes to columns {missing_in_table} but table has {db_schema_columns}. "
                          f"INSERT will fail with 'Column not found' error.",
                "impact": "All write operations fail silently or with errors"
            })
        else:
            findings["root_causes"].append({
                "cause": "table_empty_writer_not_running",
                "severity": "MEDIUM",
                "detail": "Table is empty but column names match. "
                          "Writer daemon may not be running, or no MCPs have changed since startup."
            })

    # DETERMINE ACTIONABLE FINDINGS
    if len(missing_in_table) > 0:
        findings["actionable_findings"].append({
            "type": "schema_mismatch",
            "detail": f"Writer expects columns: {writer_expected_columns}",
            "actual_columns": db_schema_columns,
            "missing": missing_in_table
        })

    if registry_count == 0:
        findings["actionable_findings"].append({
            "type": "no_data_source",
            "detail": "mcp_server_registry has no servers with tool_schema populated"
        })

    if not health_result.get("rows"):
        findings["actionable_findings"].append({
            "type": "daemon_not_heartbeating",
            "detail": "No definition/history daemon health records found"
        })

    # SUMMARY
    findings["summary"] = {
        "table_row_count": row_count,
        "writer_column_mismatch": len(missing_in_table) > 0,
        "missing_columns": missing_in_table,
        "root_cause_count": len(findings["root_causes"]),
        "actionable_count": len(findings["actionable_findings"])
    }

    return findings


def print_findings(findings: dict) -> None:
    """Pretty print findings for human review."""
    print("\n" + "=" * 80)
    print("INVESTIGATE_DEFINITION_HISTORY_GAP")
    print("=" * 80)
    print(f"Timestamp: {findings['timestamp']}\n")

    print("--- CHECK 1: TABLE SCHEMA ---")
    for check in findings["checks"]:
        if check["name"] == "table_schema":
            print(f"  Actual columns: {check['actual_columns']}")
            print(f"  Column count: {check['column_count']}")

    print("\n--- CHECK 2: ROW COUNT ---")
    for check in findings["checks"]:
        if check["name"] == "row_count":
            print(f"  mcp_definition_history rows: {check['count']}")

    print("\n--- CHECK 3: COLUMN MISMATCH ANALYSIS ---")
    for check in findings["checks"]:
        if check["name"] == "column_mismatch_analysis":
            print(f"  Writer expects: {check['writer_expected']}")
            print(f"  Table has:      {check['actual_table_columns']}")
            print(f"  Mismatch detected: {check['mismatch_detected']}")
            if check['missing_in_table']:
                print(f"  Missing in table: {check['missing_in_table']}")
            if check['extra_in_table']:
                print(f"  Extra in table:   {check['extra_in_table']}")

    print("\n--- CHECK 4: DATA SOURCE AVAILABILITY ---")
    for check in findings["checks"]:
        if check["name"] == "data_source_availability":
            print(f"  Servers with tool_schema: {check['servers_with_tool_schema']}")

    print("\n--- CHECK 5: DAEMON HEALTH ---")
    for check in findings["checks"]:
        if check["name"] == "daemon_health":
            if check["daemons"]:
                for daemon in check["daemons"]:
                    print(f"  {daemon.get('service')}: status={daemon.get('status')}, "
                          f"last_heartbeat={daemon.get('last_heartbeat')}")
            else:
                print("  No daemon health records found")

    print("\n--- CHECK 6: WRITER FILE ---")
    for check in findings["checks"]:
        if check["name"] == "writer_file":
            print(f"  File exists: {check['exists']}")
            if check.get("table_references"):
                print("  Table references found:")
                for ref in check["table_references"][:3]:
                    print(f"    {ref}")

    print("\n" + "-" * 80)
    print("ROOT CAUSES IDENTIFIED:")
    print("-" * 80)
    
    if findings["root_causes"]:
        for i, cause in enumerate(findings["root_causes"], 1):
            print(f"\n  {i}. [{cause['severity']}] {cause['cause']}")
            print(f"     {cause['detail']}")
    else:
        print("  No root causes identified.")

    print("\n" + "-" * 80)
    print("ACTIONABLE FINDINGS:")
    print("-" * 80)
    
    if findings["actionable_findings"]:
        for finding in findings["actionable_findings"]:
            print(f"\n  [{finding['type']}]")
            print(f"     {finding['detail']}")
    else:
        print("  No actionable findings.")

    print("\n" + "-" * 80)
    print("SUMMARY:")
    print("-" * 80)
    summary = findings["summary"]
    print(f"  Table row count: {summary['table_row_count']}")
    print(f"  Writer column mismatch: {summary['writer_column_mismatch']}")
    print(f"  Missing columns: {summary['missing_columns']}")
    print(f"  Root causes found: {summary['root_cause_count']}")
    print(f"  Actionable findings: {summary['actionable_count']}")

    print("\n" + "=" * 80)


def main() -> None:
    """Main entry point."""
    findings = run_diagnostics()
    print_findings(findings)
    
    # Output JSON for machine parsing
    print("\n--- JSON OUTPUT ---")
    print(json.dumps(findings, indent=2, default=str))

    # Exit code based on findings
    summary = findings["summary"]
    if summary["writer_column_mismatch"]:
        print("\n[EXIT 2] CRITICAL: Column mismatch prevents writes")
        sys.exit(2)
    elif summary["table_row_count"] == 0:
        print("\n[EXIT 1] WARNING: Table is empty")
        sys.exit(1)
    else:
        print("\n[EXIT 0] OK: No issues detected")
        sys.exit(0)


if __name__ == "__main__":
    main()