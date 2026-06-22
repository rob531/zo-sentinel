#!/usr/bin/env python3
"""
investigate_definition_history_root_cause.py

Diagnostic utility to determine why mcp_definition_history table remains empty (0 rows)
despite mcp_server_registry having 1784 records.

Checks:
1. Table structure via information_schema.columns
2. Actual table columns vs what writers attempt to insert
3. Which daemons are writing to this table (mcp_scanner / signal_analyser)
4. Column mismatch root cause identification

This is a READ-ONLY diagnostic. No writes, no rebuild proposals.
"""
import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# deps: requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
TIMEOUT = 10


def ws_query(sql: str, params: Optional[list] = None) -> dict:
    """Query write_service with retry."""
    import requests
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params

    for attempt in range(3):
        try:
            resp = requests.post(QUERY_URL, json=payload, timeout=TIMEOUT)
            if resp.status_code >= 500:
                import time
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == 2:
                return {"error": str(e), "rows": []}
            import time
            time.sleep(2 ** attempt)
    return {"error": "exhausted retries", "rows": []}


def get_table_schema(table_name: str) -> dict[str, Any]:
    """Get actual table columns from information_schema."""
    result = ws_query(
        f"SELECT column_name, data_type, ordinal_position "
        f"FROM information_schema.columns "
        f"WHERE table_name = '{table_name}' "
        f"ORDER BY ordinal_position"
    )
    columns = []
    if result.get("rows"):
        for row in result["rows"]:
            col_name = row.get("column_name", "")
            if col_name:
                columns.append(col_name)
    return {"columns": columns, "raw": result.get("rows", [])}


def get_row_count(table_name: str) -> int:
    """Get row count for a table."""
    result = ws_query(f"SELECT COUNT(*) as cnt FROM {table_name}")
    if result.get("rows"):
        row = result["rows"][0]
        cnt = row.get("cnt") if isinstance(row, dict) else row[0]
        return int(cnt) if cnt is not None else 0
    return -1


def get_service_health(pattern: str = "%") -> list[dict[str, Any]]:
    """Get service health records matching pattern."""
    result = ws_query(
        f"SELECT service, status, last_heartbeat "
        f"FROM service_health "
        f"WHERE service LIKE '{pattern}' "
        f"ORDER BY last_heartbeat DESC"
    )
    return result.get("rows", [])


def get_audit_logs_for_table(table_name: str) -> list[dict[str, Any]]:
    """Get audit log entries mentioning table."""
    result = ws_query(
        f"SELECT event_type, action, outcome, timestamp "
        f"FROM audit_log "
        f"WHERE details_json LIKE '%{table_name}%' "
        f"ORDER BY timestamp DESC "
        f"LIMIT 10"
    )
    return result.get("rows", [])


def get_sample_rows(table_name: str, limit: int = 3) -> list[dict[str, Any]]:
    """Get sample rows from a table."""
    result = ws_query(f"SELECT * FROM {table_name} LIMIT {limit}")
    return result.get("rows", [])


def identify_writer_columns_from_file(file_path: str) -> list[str]:
    """Extract column names that writers attempt to INSERT."""
    try:
        with open(file_path) as f:
            content = f.read()
    except FileNotFoundError:
        return []

    columns = set()
    # Look for INSERT INTO ... (...) patterns
    import re
    # Match: INSERT INTO table_name (col1, col2, ...)
    pattern = r'INSERT\s+INTO\s+\w+\s*\(([^)]+)\)'
    for match in re.finditer(pattern, content, re.IGNORECASE):
        cols = match.group(1)
        for col in cols.split(','):
            col = col.strip()
            if col:
                columns.add(col)

    # Also look for "rows": [{"col": value}] patterns
    pattern = r'"(server_id|definition_snapshot|changed_at|changed_by|change_reason|definition_fingerprint|definition_hash|snapshot_hash|tool_count|schema_version|mcp_id|change_type|tool_name|before_schema|after_schema|diff_summary|captured_at)"'
    for match in re.finditer(pattern, content):
        columns.add(match.group(1))

    return sorted(list(columns))


def analyze_schema_mismatch(actual_columns: list[str], writer_columns: list[str]) -> dict[str, Any]:
    """Analyze column mismatches between table and writer expectations."""
    actual_set = set(actual_columns)
    writer_set = set(writer_columns)

    missing_in_table = writer_set - actual_set
    extra_in_table = actual_set - writer_set
    matching = writer_set & actual_set

    return {
        "table_columns": actual_columns,
        "writer_columns": writer_columns,
        "missing_in_table": sorted(list(missing_in_table)),
        "extra_in_table": sorted(list(extra_in_table)),
        "matching_columns": sorted(list(matching)),
        "mismatch_detected": len(missing_in_table) > 0
    }


def run_diagnostics() -> dict[str, Any]:
    """Run all diagnostic checks and return findings."""
    findings = {
        "diagnostic": "investigate_definition_history_root_cause",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "daemons": [],
        "writers": {},
        "mismatches": {},
        "root_causes": [],
        "summary": {}
    }

    # 1. Get ACTUAL table schema from information_schema
    history_schema = get_table_schema("mcp_definition_history")
    registry_schema = get_table_schema("mcp_server_registry")

    findings["tables"]["mcp_definition_history"] = {
        "schema": history_schema,
        "row_count": get_row_count("mcp_definition_history"),
        "samples": get_sample_rows("mcp_definition_history")
    }

    findings["tables"]["mcp_server_registry"] = {
        "schema": registry_schema,
        "row_count": get_row_count("mcp_server_registry")
    }

    # 2. Check service health for daemons
    definition_daemons = get_service_health("%definition%")
    history_daemons = get_service_health("%history%")
    change_daemons = get_service_health("%change%")
    findings["daemons"] = {
        "definition": definition_daemons,
        "history": history_daemons,
        "change": change_daemons
    }

    # 3. Check audit logs
    findings["audit_logs"] = get_audit_logs_for_table("mcp_definition_history")

    # 4. Identify writer columns from source files
    writer_files = [
        "/home/workspace/zo_sentinel/mcp_definition_history_writer.py",
        "/home/workspace/zo_sentinel/mcp_definition_history_filler.py",
        "/home/workspace/zo_sentinel/definition_change_history_writer.py",
        "/home/workspace/zo_sentinel/definition_change_history_writer_v2.py",
    ]

    for file_path in writer_files:
        import os
        file_name = os.path.basename(file_path)
        writer_cols = identify_writer_columns_from_file(file_path)
        findings["writers"][file_name] = {
            "columns": writer_cols,
            "exists": os.path.exists(file_path)
        }

        # Analyze mismatch
        mismatch = analyze_schema_mismatch(
            history_schema["columns"],
            writer_cols
        )
        findings["mismatches"][file_name] = mismatch

    # 5. Determine root causes
    actual_cols = history_schema["columns"]
    row_count = get_row_count("mcp_definition_history")

    # Check if any writer has a match with actual table
    has_any_match = False
    worst_mismatch = {"file": "", "missing": []}

    for file_name, mismatch in findings["mismatches"].items():
        if mismatch["matching_columns"]:
            has_any_match = True
        if len(mismatch["missing_in_table"]) > len(worst_mismatch["missing"]):
            worst_mismatch = {
                "file": file_name,
                "missing": mismatch["missing_in_table"]
            }

    if row_count == 0:
        if not has_any_match:
            findings["root_causes"].append({
                "cause": "complete_column_mismatch",
                "severity": "CRITICAL",
                "detail": f"ALL writers attempt to insert columns that don't exist in mcp_definition_history table.",
                "actual_columns": actual_cols,
                "worst_offender": worst_mismatch["file"],
                "missing_columns": worst_mismatch["missing"],
                "impact": "Every INSERT will fail with 'Column not found' error. Table will remain empty."
            })
        else:
            findings["root_causes"].append({
                "cause": "partial_column_mismatch",
                "severity": "HIGH",
                "detail": "Some columns match but INSERT will still fail due to missing required columns."
            })

    # Check for which daemons should be writing
    if definition_daemons or history_daemons or change_daemons:
        findings["root_causes"].append({
            "cause": "writer_daemon_may_be_running",
            "severity": "INFO",
            "detail": "Daemons exist but their INSERTs fail due to column mismatch."
        })
    else:
        findings["root_causes"].append({
            "cause": "no_writer_daemon_heartbeat",
            "severity": "MEDIUM",
            "detail": "No definition/history/change daemons are heartbeating. "
                      "Even if daemons existed, their INSERTs would fail."
        })

    # 6. Summary
    findings["summary"] = {
        "history_row_count": row_count,
        "registry_row_count": get_row_count("mcp_server_registry"),
        "actual_table_columns": actual_cols,
        "writers_found": len(findings["writers"]),
        "writers_with_mismatch": sum(1 for m in findings["mismatches"].values() if m["mismatch_detected"]),
        "root_cause_count": len(findings["root_causes"]),
        "critical_cause": next((c["cause"] for c in findings["root_causes"] if c["severity"] == "CRITICAL"), None)
    }

    return findings


def print_report(findings: dict[str, Any]) -> None:
    """Print human-readable diagnostic report."""
    print("\n" + "=" * 80)
    print("INVESTIGATE_DEFINITION_HISTORY_ROOT_CAUSE")
    print("=" * 80)
    print(f"Timestamp: {findings['timestamp']}\n")

    # Table info
    print("--- TABLE STRUCTURE (from information_schema) ---")
    history = findings["tables"]["mcp_definition_history"]
    registry = findings["tables"]["mcp_server_registry"]
    print(f"  mcp_definition_history rows: {history['row_count']}")
    print(f"  mcp_server_registry rows:   {registry['row_count']}")
    print(f"\n  ACTUAL mcp_definition_history columns:")
    for col in history["schema"]["columns"]:
        print(f"    - {col}")

    # Writer analysis
    print("\n--- WRITER COLUMN ANALYSIS ---")
    for file_name, info in findings["writers"].items():
        if not info["exists"]:
            continue
        mismatch = findings["mismatches"].get(file_name, {})
        print(f"\n  {file_name}:")
        print(f"    Writer attempts: {info['columns']}")
        if mismatch.get("matching_columns"):
            print(f"    Matches:         {mismatch['matching_columns']}")
        if mismatch.get("missing_in_table"):
            print(f"    MISSING from table: {mismatch['missing_in_table']}")

    # Root causes
    print("\n" + "-" * 80)
    print("ROOT CAUSE IDENTIFICATION:")
    print("-" * 80)

    for cause in findings["root_causes"]:
        print(f"\n  [{cause['severity']}] {cause['cause']}")
        print(f"     {cause['detail']}")
        if cause.get("actual_columns"):
            print(f"     Table has: {cause['actual_columns']}")
        if cause.get("missing_columns"):
            print(f"     Writer needs: {cause['missing_columns']}")
        if cause.get("worst_offender"):
            print(f"     Worst offender: {cause['worst_offender']}")

    # Summary
    print("\n" + "-" * 80)
    print("SUMMARY:")
    print("-" * 80)
    summary = findings["summary"]
    print(f"  History table rows:    {summary['history_row_count']}")
    print(f"  Registry table rows:  {summary['registry_row_count']}")
    print(f"  Writers with mismatch: {summary['writers_with_mismatch']}/{summary['writers_found']}")
    print(f"  Critical root cause:   {summary['critical_cause'] or 'None'}")
    print("\n  MISSING INSERT PATTERN:")
    print("  The writers need to insert into the ACTUAL table columns:")
    print(f"    {summary['actual_table_columns']}")
    print("  But none of the writers attempt these columns.")

    print("\n" + "=" * 80)


def main() -> None:
    """Main entry point."""
    findings = run_diagnostics()
    print_report(findings)

    # Output JSON
    print("\n--- JSON OUTPUT ---")
    print(json.dumps(findings, indent=2, default=str))

    # Exit code
    if findings["summary"]["critical_cause"] == "complete_column_mismatch":
        print("\n[EXIT 2] CRITICAL: Complete column mismatch - no writes possible")
        sys.exit(2)
    elif findings["summary"]["history_row_count"] == 0:
        print("\n[EXIT 1] WARNING: Table is empty")
        sys.exit(1)
    else:
        print("\n[EXIT 0] OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
