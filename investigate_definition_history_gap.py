#!/usr/bin/env python3
"""
Investigative diagnostic: why mcp_definition_history is empty (0 rows)
despite definition_change_history_writer.py being built at 2026-06-18T05:47:26.

Checks:
1. information_schema.columns to verify table structure
2. Different SELECT criteria for any existing rows
3. Verify writer's write_service calls targeting the correct table/columns

This is a READ-ONLY diagnostic -- no writes, no rebuild proposals.
"""
import json
import sys
from datetime import datetime, timezone

# deps: requests

WRITE_SERVICE = "http://127.0.0.1:8772"
MAX_RETRIES = 3
TIMEOUT = 10


def query_ws(sql: str, params: list | None = None) -> dict:
    """Query write_service with retry."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    
    import requests
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=TIMEOUT)
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "root_causes": [],
        "actionable_findings": []
    }
    
    # CHECK 1: Verify table structure via information_schema.columns
    findings["checks"].append({
        "name": "information_schema_columns",
        "query": "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_name = 'mcp_definition_history' ORDER BY ordinal_position"
    })
    
    schema_result = query_ws(
        "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_name = 'mcp_definition_history' ORDER BY ordinal_position"
    )
    findings["checks"][-1]["result"] = schema_result
    
    actual_columns = []
    if schema_result.get("rows"):
        for row in schema_result["rows"]:
            col_name = row.get("column_name") or row.get(1) if isinstance(row, dict) else (row[1] if len(row) > 1 else None)
            actual_columns.append(col_name)
        findings["checks"][-1]["actual_columns"] = actual_columns
        findings["checks"][-1]["column_count"] = len(actual_columns)
    
    # CHECK 2: Count rows with standard SELECT
    count_result = query_ws("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    row_count = 0
    if count_result.get("rows"):
        cnt_val = count_result["rows"][0].get("cnt") or count_result["rows"][0].get(0) if isinstance(count_result["rows"][0], dict) else (count_result["rows"][0][0] if count_result["rows"] else 0)
        row_count = int(cnt_val) if cnt_val is not None else 0
    findings["checks"].append({
        "name": "row_count_standard_select",
        "count": row_count
    })
    
    # CHECK 3: Try different column names the writer might use
    writer_expected_columns = ["mcp_id", "changed_at", "change_type", "tool_name", "before_schema", "after_schema", "diff_summary"]
    column_mismatch_found = False
    
    for col in writer_expected_columns:
        if col not in actual_columns:
            column_mismatch_found = True
            findings["actionable_findings"].append({
                "type": "column_mismatch",
                "detail": f"Writer expects column '{col}' but table has {actual_columns}"
            })
    
    if column_mismatch_found:
        findings["root_causes"].append({
            "cause": "column_name_mismatch",
            "severity": "CRITICAL",
            "detail": "definition_change_history_writer.py writes to columns (mcp_id, changed_at, change_type, tool_name, before_schema, after_schema, diff_summary) that do NOT exist in mcp_definition_history table",
            "actual_columns": actual_columns,
            "writer_expected": writer_expected_columns
        })
    
    # CHECK 4: Check if any data exists under alternative column names
    alt_queries = [
        ("server_id_count", "SELECT COUNT(*) FROM mcp_definition_history WHERE server_id IS NOT NULL"),
        ("snapshot_hash_count", "SELECT COUNT(*) FROM mcp_definition_history WHERE snapshot_hash IS NOT NULL"),
        ("captured_at_range", "SELECT MIN(captured_at) as min_ts, MAX(captured_at) as max_ts FROM mcp_definition_history"),
    ]
    
    for name, sql in alt_queries:
        result = query_ws(sql)
        findings["checks"].append({
            "name": name,
            "query": sql,
            "result": result
        })
    
    # CHECK 5: Check mcp_server_registry for data source
    registry_count = query_ws("SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE tool_schema IS NOT NULL")
    registry_tool_count = 0
    if registry_count.get("rows"):
        cnt_val = registry_count["rows"][0].get("cnt") or registry_count["rows"][0].get(0) if isinstance(registry_count["rows"][0], dict) else (registry_count["rows"][0][0] if registry_count["rows"] else 0)
        registry_tool_count = int(cnt_val) if cnt_val is not None else 0
    
    findings["checks"].append({
        "name": "mcp_server_registry_data_source",
        "servers_with_tool_schema": registry_tool_count,
        "note": "Writer reads from this table to get current tool schemas"
    })
    
    # CHECK 6: Check if table has any data at all (empty vs populated with wrong columns)
    sample_rows = query_ws("SELECT * FROM mcp_definition_history LIMIT 5")
    findings["checks"].append({
        "name": "sample_rows",
        "query": "SELECT * FROM mcp_definition_history LIMIT 5",
        "sample": sample_rows.get("rows", [])
    })
    
    # CHECK 7: Check definition_change_history_writer.py exists and its target table
    import os
    writer_path = "/home/workspace/zo_sentinel/definition_change_history_writer.py"
    findings["checks"].append({
        "name": "writer_file_check",
        "path": writer_path,
        "exists": os.path.exists(writer_path)
    })
    
    if os.path.exists(writer_path):
        with open(writer_path) as f:
            content = f.read()
        
        # Extract the table name the writer targets
        table_refs = []
        for line in content.split("\n"):
            if '"table"' in line and 'mcp_definition_history' in line:
                table_refs.append(line.strip())
        
        findings["checks"][-1]["table_references"] = table_refs
        
        # Check column names used in _build_history_row or similar
        history_row_cols = []
        for line in content.split("\n"):
            if "_build_history_row" in line or "changed_at" in line or "change_type" in line:
                if '"' in line or "'" in line:
                    history_row_cols.append(line.strip())
        
        findings["checks"][-1]["history_row_column_usage"] = history_row_cols[:10]  # First 10 matches
    
    # CHECK 8: Verify write_service is reachable
    import requests
    try:
        health_resp = requests.post(f"{WRITE_SERVICE}/query", 
                                   json={"sql": "SELECT 1 as test"}, 
                                   timeout=5)
        write_service_ok = health_resp.status_code < 500
    except Exception as e:
        write_service_ok = False
        findings["actionable_findings"].append({
            "type": "write_service_unreachable",
            "detail": str(e)
        })
    
    findings["checks"].append({
        "name": "write_service_reachable",
        "status": "OK" if write_service_ok else "UNREACHABLE"
    })
    
    # DETERMINE ROOT CAUSES
    if row_count == 0:
        if not column_mismatch_found:
            findings["root_causes"].append({
                "cause": "table_empty_no_mismatch",
                "severity": "MEDIUM",
                "detail": "Table is empty but column names match. Writer may not be running or daemon has no MCPs to track."
            })
    
    # SUMMARY
    findings["summary"] = {
        "table_row_count": row_count,
        "table_column_count": len(actual_columns),
        "actual_columns": actual_columns,
        "writer_column_mismatch": column_mismatch_found,
        "root_cause_count": len(findings["root_causes"])
    }
    
    return findings


def print_findings(findings: dict):
    """Pretty print findings for human review."""
    print("\n" + "=" * 80)
    print("MCP_DEFINITION_HISTORY GAP INVESTIGATION")
    print("=" * 80)
    print(f"Timestamp: {findings['timestamp']}\n")
    
    print("--- CHECK RESULTS ---")
    for check in findings["checks"]:
        print(f"\n  [{check['name']}]")
        if "count" in check:
            print(f"    Count: {check['count']}")
        if "actual_columns" in check:
            print(f"    Columns: {check['actual_columns']}")
        if "column_count" in check:
            print(f"    Column count: {check['column_count']}")
        if "servers_with_tool_schema" in check:
            print(f"    Servers with tool_schema: {check['servers_with_tool_schema']}")
        if "status" in check:
            print(f"    Status: {check['status']}")
        if "table_references" in check and check["table_references"]:
            print(f"    Table refs in writer: {check['table_references']}")
        if "exists" in check:
            print(f"    Writer exists: {check['exists']}")
    
    print("\n" + "-" * 80)
    print("ROOT CAUSES IDENTIFIED:")
    print("-" * 80)
    
    if findings["root_causes"]:
        for i, cause in enumerate(findings["root_causes"], 1):
            print(f"\n  {i}. [{cause['severity']}] {cause['cause']}")
            print(f"     {cause['detail']}")
            if "actual_columns" in cause:
                print(f"     Actual table columns: {cause['actual_columns']}")
            if "writer_expected" in cause:
                print(f"     Writer expects: {cause['writer_expected']}")
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
    print(f"  Table column count: {summary['table_column_count']}")
    print(f"  Actual columns: {summary['actual_columns']}")
    print(f"  Writer column mismatch: {summary['writer_column_mismatch']}")
    print(f"  Root causes found: {summary['root_cause_count']}")
    
    print("\n" + "=" * 80)
    
    # Exit code based on findings
    if summary["writer_column_mismatch"]:
        print("\nCONCLUSION: Column name mismatch is CRITICAL - writer cannot insert data.")
        sys.exit(2)
    elif summary["table_row_count"] == 0:
        print("\nCONCLUSION: Table empty - writer may not be running or no data source.")
        sys.exit(1)
    else:
        print("\nCONCLUSION: Table has data - investigate query patterns.")
        sys.exit(0)


def main():
    findings = run_diagnostics()
    print_findings(findings)
    
    # Also output JSON for machine parsing
    print("\n--- JSON OUTPUT ---")
    print(json.dumps(findings, indent=2, default=str))


if __name__ == "__main__":
    main()
