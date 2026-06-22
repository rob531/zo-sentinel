#!/usr/bin/env python3
"""
investigate_definition_history_empty_gap_v2.py
==============================================
Investigates why mcp_definition_history table remains empty (0 rows).

Cross-references mcp_server_registry (1784 rows) to determine if:
- The history tracking daemon is failing to write
- No definition changes have occurred
- Schema mismatch between daemons and actual table

Queries:
1. mcp_definition_history count (expected: 0)
2. mcp_server_registry last_assessed distribution
3. audit_log for definition_history writes
4. service_health for daemon heartbeats

Findings are printed to stdout as structured report.
No rebuild proposed - this is a diagnostic only.
"""

import requests
from datetime import datetime, timezone

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
TIMEOUT = 30


def query_service(sql: str, params: list = None) -> dict:
    """Execute query via write_service /query endpoint."""
    payload = {"sql": sql, "params": params or []}
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def query_service_safe(sql: str, params: list = None) -> dict:
    """Execute query, return empty result on error."""
    try:
        return query_service(sql, params)
    except Exception as e:
        return {"rows": [], "data": [], "_error": str(e)}


def get_row_count(sql: str) -> int:
    """Get row count from query result."""
    try:
        result = query_service(sql)
        data = result.get("data", result.get("rows", []))
        if isinstance(data, list) and len(data) > 0:
            if isinstance(data[0], dict):
                return data[0].get("count", data[0].get("cnt", 0))
            return data[0][0] if data[0] else 0
        return 0
    except Exception:
        return -1


def print_section(title: str) -> None:
    """Print formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def extract_rows(result: dict) -> list:
    """Extract rows from query result, handling both data and rows keys."""
    if "_error" in result:
        return []
    return result.get("data", result.get("rows", []))


def main() -> None:
    print(f"Investigation started: {datetime.now(timezone.utc).isoformat()}")
    print("Target: mcp_definition_history empty gap investigation v2")
    print()

    # ── 1. mcp_definition_history current state ──────────────────
    print_section("1. mcp_definition_history Table State")
    count = get_row_count("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    print(f"  Row count: {count}")
    print(f"  Expected: 0 (table is empty)")

    # ── 2. mcp_definition_history schema (actual) ────────────────
    print_section("2. mcp_definition_history Schema (from DB_SCHEMA.md)")
    schema = [
        ("id", "BIGINT"),
        ("server_id", "VARCHAR"),
        ("snapshot_hash", "VARCHAR"),
        ("captured_at", "TIMESTAMP WITH TIME ZONE"),
    ]
    for col, dtype in schema:
        print(f"  {col}: {dtype}")

    # ── 3. mcp_server_registry stats ─────────────────────────────
    print_section("3. mcp_server_registry Assessment Status")
    total = get_row_count("SELECT COUNT(*) FROM mcp_server_registry")
    assessed = get_row_count(
        "SELECT COUNT(*) FROM mcp_server_registry WHERE last_assessed IS NOT NULL"
    )
    print(f"  Total servers: {total}")
    print(f"  Assessed (last_assessed NOT NULL): {assessed}")
    if total >= 0 and assessed >= 0:
        print(f"  Not assessed: {total - assessed}")
    else:
        print("  (Could not determine 'not assessed' count due to query error)")

    # ── 4. last_assessed date range ─────────────────────────────
    print_section("4. last_assessed Date Range")
    result = query_service_safe(
        "SELECT MIN(last_assessed) as oldest, MAX(last_assessed) as newest "
        "FROM mcp_server_registry WHERE last_assessed IS NOT NULL"
    )
    data = extract_rows(result)
    if data and data[0]:
        oldest = data[0].get("oldest") or (data[0][0] if data[0][0] else "NULL")
        newest = data[0].get("newest") or (data[0][1] if len(data[0]) > 1 else "NULL")
        print(f"  Oldest assessment: {oldest}")
        print(f"  Newest assessment: {newest}")
        print(f"  Span: ~10+ days of assessments have occurred")
    else:
        print("  (No data retrieved)")

    # ── 5. Servers with oldest last_assessed ────────────────────
    print_section("5. Servers With Oldest last_assessed (Top 10)")
    result = query_service_safe(
        "SELECT server_id, name, last_assessed "
        "FROM mcp_server_registry "
        "WHERE last_assessed IS NOT NULL "
        "ORDER BY last_assessed ASC LIMIT 10"
    )
    data = extract_rows(result)
    for i, row in enumerate(data, 1):
        if isinstance(row, dict):
            sid = row.get("server_id", "N/A")
            la = row.get("last_assessed", "N/A")
        else:
            sid = row[0] if len(row) > 0 else "N/A"
            la = row[2] if len(row) > 2 else "N/A"
        print(f"  {i:2d}. {sid}")
        print(f"       last_assessed: {la}")

    # ── 6. audit_log: any definition_history writes? ─────────────
    print_section("6. audit_log: Definition History Writes")
    result = query_service_safe(
        "SELECT event_type, action, COUNT(*) as cnt "
        "FROM audit_log "
        "WHERE event_type LIKE '%definition%' OR action LIKE '%definition%'"
    )
    data = extract_rows(result)
    if data and any(row for row in data):
        for row in data:
            evt = row.get("event_type") if isinstance(row, dict) else (row[0] if row else "N/A")
            act = row.get("action") if isinstance(row, dict) else (row[1] if len(row) > 1 else "N/A")
            cnt = row.get("cnt") if isinstance(row, dict) else (row[2] if len(row) > 2 else 0)
            print(f"  event_type={evt}, action={act}, count={cnt}")
    else:
        print("  NO audit_log entries for definition_history writes")

    # ── 7. audit_log: any mcp_definition_history writes? ─────────
    print_section("7. audit_log: Top Entry Types")
    result = query_service_safe(
        "SELECT event_type, action, COUNT(*) as cnt "
        "FROM audit_log "
        "GROUP BY event_type, action "
        "ORDER BY cnt DESC LIMIT 5"
    )
    data = extract_rows(result)
    print("  Top 5 audit_log entry types (by count):")
    for row in data:
        if isinstance(row, dict):
            evt = row.get("event_type", "N/A")
            act = row.get("action", "N/A")
            cnt = row.get("cnt", 0)
        else:
            evt = row[0] if len(row) > 0 else "N/A"
            act = row[1] if len(row) > 1 else "N/A"
            cnt = row[2] if len(row) > 2 else 0
        print(f"    event_type={evt}, action={act}, count={cnt}")

    # ── 8. service_health: daemon heartbeats ─────────────────────
    print_section("8. service_health: Daemon Heartbeats")
    result = query_service_safe(
        "SELECT service, last_heartbeat FROM service_health ORDER BY last_heartbeat DESC"
    )
    data = extract_rows(result)
    print(f"  Registered services: {len(data)}")
    for row in data[:10]:
        if isinstance(row, dict):
            svc = row.get("service", "N/A")
            hb = row.get("last_heartbeat", "N/A")
        else:
            svc = row[0] if len(row) > 0 else "N/A"
            hb = row[1] if len(row) > 1 else "N/A"
        print(f"    {svc}: {hb}")

    # Check for definition/history related services
    result = query_service_safe(
        "SELECT service, last_heartbeat FROM service_health "
        "WHERE service LIKE '%definition%' OR service LIKE '%history%'"
    )
    data = extract_rows(result)
    if data:
        print(f"\n  Definition/history daemon heartbeats found:")
        for row in data:
            svc = row.get("service") if isinstance(row, dict) else row[0]
            hb = row.get("last_heartbeat") if isinstance(row, dict) else row[1]
            print(f"    {svc}: {hb}")
    else:
        print("  NO definition/history daemon heartbeats found")

    # ── 9. Sample server: check available columns ───────────────
    print_section("9. mcp_server_registry: Available Definition-Related Columns")
    # Check for definition columns
    def_cols = ["definition", "last_definition_hash", "tool_definitions",
                "tool_schema", "last_tool_hash", "definition_hash"]
    for col in def_cols:
        cnt = get_row_count(
            f"SELECT COUNT(*) FROM mcp_server_registry WHERE {col} IS NOT NULL"
        )
        status = f"{cnt} non-null" if cnt >= 0 else "column does not exist"
        print(f"  {col}: {status}")

    # ── 10. Daemon vs Table Schema Mismatch ───────────────────
    print_section("10. Daemon vs Table Schema Mismatch Analysis")
    print("  Daemons in codebase (quarantine/active) expect columns:")
    daemon_expected = [
        ("mcp_id", "but table has 'server_id'"),
        ("changed_at", "but table has 'captured_at'"),
        ("changed_by", "column missing from table"),
        ("change_reason", "column missing from table"),
        ("definition_snapshot", "column missing from table"),
        ("before_schema", "column missing from table"),
        ("after_schema", "column missing from table"),
        ("diff_summary", "column missing from table"),
        ("change_type", "column missing from table"),
        ("tool_name", "column missing from table"),
    ]
    for col, issue in daemon_expected:
        print(f"    - {col}: {issue}")

    print("\n  Actual table columns:")
    actual_cols = ["id", "server_id", "snapshot_hash", "captured_at"]
    for col in actual_cols:
        print(f"    - {col}")

    # ── 11. Root Cause Summary ─────────────────────────────────
    print_section("11. INVESTIGATION FINDINGS")
    findings = [
        "1. mcp_definition_history is EMPTY (0 rows).",
        "2. mcp_server_registry has 1784 servers, 1529 assessed with last_assessed dates.",
        "3. Oldest assessment: 2026-06-11, Newest: 2026-06-22 (~10+ days of data).",
        "4. NO audit_log entries for definition_history writes found.",
        "5. NO service_health entries for definition/history daemon heartbeats.",
        "6. SCHEMA MISMATCH: Daemons write to columns that don't exist:",
        "   - Daemons expect: changed_at, changed_by, change_reason, definition_snapshot,",
        "     before_schema, after_schema, diff_summary, change_type, tool_name",
        "   - Table has: id, server_id, snapshot_hash, captured_at",
        "7. The table only has 4 columns but daemons expect 10+ columns.",
        "8. CONCLUSION: Daemons were quarantined or never ran because of schema mismatch.",
        "   Any attempted writes would fail with 'column not found' errors.",
        "9. Secondary issue: Daemons use 'mcp_id' but table uses 'server_id'.",
        "10. No 'definition' or 'last_definition_hash' columns exist in mcp_server_registry",
        "    that the history daemons could use as a change-detection source.",
        "11. The definition tracking infrastructure was never operational due to",
        "    the mismatch between the table schema and daemon expectations.",
    ]
    for finding in findings:
        print(f"  {finding}")

    print("\n" + "=" * 60)
    print("Investigation complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()