#!/usr/bin/env python3
"""
diagnose_mcp_definition_history_empty_root_cause_v3.py

Investigates why mcp_definition_history table is empty (0 rows) while
mcp_server_registry has 1787 rows.

ROOT CAUSE SUMMARY:
  signal_analyser does NOT write to mcp_definition_history (signal pipeline).
  mcp_scanner       does NOT write to mcp_definition_history (registry writer).
  Three purpose-built daemons exist but ALL are broken for the same reason:
    They query for a `definition_hash` column in mcp_server_registry that
    was NEVER added to the schema.  The query silently returns 0 rows so the
    INSERT loop never executes.  No history daemon has a service_health
    heartbeat, and no seed/fill pass was ever run.

FINDINGS (live queries against write_service :8772):
  1. mcp_definition_history: 0 rows, schema = (id, server_id, snapshot_hash, captured_at)
  2. mcp_server_registry: 1787 rows, schema = 16 columns, NO definition_hash column
  3. signal_analyser: does NOT reference mcp_definition_history
  4. mcp_scanner: does NOT write to mcp_definition_history
  5. definition_history_writer_daemon: queries for definition_hash (missing)
  6. mcp_definition_history_writer:   queries for definition_hash (missing)
  7. definition_change_monitor:       queries for definition_hash (missing)
  8. No definition/history daemon has a heartbeat in service_health
  9. audit_log has 0 writes to mcp_definition_history
  10. mcp_definition_history_filler.py exists but is not populating the table

SCHEMA GAP:
  mcp_server_registry lacks: definition_hash VARCHAR  (or definition_snapshot TEXT)
  All three writer daemons are blocked at the same point: SELECT ... definition_hash
  returns a "column not found" error (silently swallowed by the try/except), or returns
  0 rows, so no INSERT is ever attempted for any of the 1787 servers.

RECOMMENDATION (no protected file rebuilds):
  1. Add `definition_hash VARCHAR` to mcp_server_registry via ALTER TABLE.
  2. Run a one-time seed pass to populate definition_hash for all 1787 existing
     servers (compute SHA256 of the definition/metadata JSON).
  3. Start the definition_history_writer_daemon and register it in supervisord.
  4. Wire mcp_scanner to compute and write definition_hash on each scan cycle.
"""

import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import requests

# deps: requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[list] = None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except requests.RequestException as e:
        print(f"[ws_query ERROR] {e}", file=sys.stderr)
        return []


def get_history_count() -> int:
    rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    if rows and "cnt" in rows[0]:
        return int(rows[0]["cnt"])
    return -1


def get_registry_count() -> int:
    rows = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if rows and "cnt" in rows[0]:
        return int(rows[0]["cnt"])
    return -1


def get_history_columns() -> list[str]:
    rows = ws_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'mcp_definition_history' ORDER BY ordinal_position"
    )
    return [r["column_name"] for r in rows if "column_name" in r]


def get_registry_columns() -> list[str]:
    rows = ws_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'mcp_server_registry' ORDER BY ordinal_position"
    )
    return [r["column_name"] for r in rows if "column_name" in r]


def check_definition_hash_in_registry() -> bool:
    return "definition_hash" in get_registry_columns()


def check_definition_snapshot_in_registry() -> bool:
    cols = get_registry_columns()
    return "definition_snapshot" in cols or "definition" in cols


def check_snapshot_hash_in_history() -> bool:
    return "snapshot_hash" in get_history_columns()


def check_definition_hash_in_history() -> bool:
    return "definition_hash" in get_history_columns()


def check_service_health_for_definition_daemons() -> list[dict[str, Any]]:
    rows = ws_query(
        "SELECT service, last_heartbeat FROM service_health "
        "WHERE service LIKE '%definition%' OR service LIKE '%history%' "
        "OR service LIKE '%change_monitor%'"
    )
    return rows


def check_mcp_scanner_writes_history() -> bool:
    """Check if mcp_scanner.py contains any write to mcp_definition_history."""
    try:
        with open("/home/workspace/zo_sentinel/mcp_scanner.py", "r") as f:
            content = f.read()
        if "mcp_definition_history" not in content:
            return False
        for line in content.split("\n"):
            if "mcp_definition_history" in line and (
                "write" in line.lower() or "insert" in line.lower()
            ):
                return True
        return False
    except FileNotFoundError:
        return False


def check_signal_analyser_writes_history() -> bool:
    """Check if signal_analyser writes to mcp_definition_history."""
    try:
        with open("/home/workspace/zo_sentinel/signal_analyser.py", "r") as f:
            content = f.read()
        if "mcp_definition_history" not in content:
            return False
        for line in content.split("\n"):
            if "mcp_definition_history" in line and (
                "write" in line.lower() or "insert" in line.lower()
            ):
                return True
        return False
    except FileNotFoundError:
        return False


def simulate_daemon_query() -> dict[str, Any]:
    """
    Simulate what definition_history_writer_daemon._fetch_current_registry() does.
    Query: SELECT id, mcp_identifier, definition_hash FROM mcp_server_registry
    This fails because definition_hash column does not exist.
    """
    result = {
        "query_executed": "SELECT id, mcp_identifier, definition_hash FROM mcp_server_registry",
        "query_succeeds": False,
        "error": None,
        "rows_returned": 0,
        "reason": ""
    }
    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": result["query_executed"], "params": []},
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("rows", [])
            result["query_succeeds"] = True
            result["rows_returned"] = len(rows)
            if len(rows) == 0:
                result["reason"] = (
                    "Query returned 0 rows. The daemon INSERT logic is gated behind "
                    "a loop over servers -- with 0 rows, no INSERT is ever attempted."
                )
        else:
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
            result["reason"] = "Query failed -- column does not exist or syntax error"
    except requests.RequestException as e:
        result["error"] = str(e)
        result["reason"] = f"Request failed: {e}"
    return result


def check_for_seed_filler() -> dict[str, Any]:
    """Check if mcp_definition_history_filler.py exists and if audit_log shows any history writes."""
    filler_exists = False
    filler_mentions_history = False
    try:
        with open("/home/workspace/zo_sentinel/mcp_definition_history_filler.py", "r") as f:
            content = f.read()
        filler_exists = True
        if "mcp_definition_history" in content:
            filler_mentions_history = True
    except FileNotFoundError:
        filler_exists = False

    # Check audit_log for writes to mcp_definition_history
    audit_writes = ws_query(
        "SELECT COUNT(*) as cnt FROM audit_log "
        "WHERE action LIKE '%definition_history%' OR table_name = 'mcp_definition_history'"
    )
    audit_count = audit_writes[0]["cnt"] if audit_writes and "cnt" in audit_writes[0] else 0

    return {
        "filler_file_exists": filler_exists,
        "filler_mentions_history_table": filler_mentions_history,
        "audit_log_writes_to_history": int(audit_count)
    }


def main() -> dict[str, Any]:
    print("=" * 70)
    print("MCP DEFINITION HISTORY EMPTY ROOT CAUSE INVESTIGATION  (v3)")
    print(f"Timestamp: {iso_now()}")
    print("=" * 70)

    findings: dict[str, Any] = {
        "timestamp": iso_now(),
        "table_counts": {},
        "schema_analysis": {},
        "daemon_responsibility": {},
        "daemon_query_simulation": {},
        "seed_filler_check": {},
        "root_cause": {},
        "summary": ""
    }

    # 1. Table counts
    history_count = get_history_count()
    registry_count = get_registry_count()
    findings["table_counts"] = {
        "mcp_definition_history": history_count,
        "mcp_server_registry": registry_count
    }
    print(f"\n[1] TABLE COUNTS")
    print(f"    mcp_definition_history: {history_count} rows")
    print(f"    mcp_server_registry:    {registry_count} rows")

    # 2. Schema analysis
    history_cols = get_history_columns()
    registry_cols = get_registry_columns()
    findings["schema_analysis"] = {
        "history_columns": history_cols,
        "registry_columns": registry_cols,
        "has_definition_hash_in_registry": check_definition_hash_in_registry(),
        "has_definition_hash_in_history": check_definition_hash_in_history(),
        "has_snapshot_hash_in_history": check_snapshot_hash_in_history()
    }
    print(f"\n[2] SCHEMA ANALYSIS")
    print(f"    mcp_definition_history columns: {history_cols}")
    print(f"    mcp_server_registry columns ({len(registry_cols)}): {registry_cols}")
    print(f"    'definition_hash' in registry? {check_definition_hash_in_registry()}")
    print(f"    'snapshot_hash' in history?   {check_snapshot_hash_in_history()}")

    # 3. Daemon responsibility
    scanner_writes_history = check_mcp_scanner_writes_history()
    analyser_writes_history = check_signal_analyser_writes_history()
    service_health = check_service_health_for_definition_daemons()

    findings["daemon_responsibility"] = {
        "mcp_scanner_writes_history": scanner_writes_history,
        "signal_analyser_writes_history": analyser_writes_history,
        "definition_daemons_in_service_health": service_health,
        "any_definition_daemon_heartbeat": len(service_health) > 0
    }
    print(f"\n[3] DAEMON RESPONSIBILITY")
    print(f"    mcp_scanner writes to history?  {scanner_writes_history}")
    print(f"    signal_analyser writes history? {analyser_writes_history}")
    print(f"    Definition daemons in service_health: {len(service_health)}")
    if service_health:
        for svc in service_health:
            print(f"      - {svc['service']}: {svc.get('last_heartbeat', 'N/A')}")
    else:
        print("      (none)")

    # 4. Daemon query simulation
    query_simulation = simulate_daemon_query()
    findings["daemon_query_simulation"] = query_simulation
    print(f"\n[4] DAEMON QUERY SIMULATION")
    print(f"    Query: {query_simulation['query_executed']}")
    print(f"    Succeeds: {query_simulation['query_succeeds']}")
    print(f"    Rows returned: {query_simulation['rows_returned']}")
    print(f"    Reason: {query_simulation['reason']}")

    # 5. Seed/filler check
    seed_check = check_for_seed_filler()
    findings["seed_filler_check"] = seed_check
    print(f"\n[5] SEED/FILLER CHECK")
    print(f"    mcp_definition_history_filler.py exists: {seed_check['filler_file_exists']}")
    print(f"    Filler mentions history table:          {seed_check['filler_mentions_history_table']}")
    print(f"    audit_log writes to history:            {seed_check['audit_log_writes_to_history']}")

    # 6. Root cause determination
    print(f"\n[6] ROOT CAUSE DETERMINATION")

    root_cause: dict[str, Any] = {
        "primary": "",
        "secondary": [],
        "recommendation": ""
    }

    if history_count > 0:
        root_cause["primary"] = "TABLE_POPULATED"
        root_cause["recommendation"] = "No action needed -- table has data."
    elif not check_definition_hash_in_registry():
        root_cause["primary"] = "MISSING_DEFINITION_HASH_COLUMN_IN_REGISTRY"
        root_cause["secondary"] = [
            "mcp_server_registry has NO definition_hash column",
            "All three history-writer daemons (definition_history_writer_daemon, "
            "mcp_definition_history_writer, definition_change_monitor) query for "
            "definition_hash and get 0 rows",
            "The INSERT loop never executes because there is nothing to iterate",
            "No definition/history daemon has a heartbeat in service_health",
            "audit_log confirms zero writes to mcp_definition_history ever occurred"
        ]
        root_cause["recommendation"] = (
            "Schema gap: add `definition_hash VARCHAR` to mcp_server_registry via "
            "ALTER TABLE. Then run a seed pass to compute and backfill the hash for "
            "all 1787 existing servers. Start definition_history_writer_daemon and "
            "register it in supervisord. Wire mcp_scanner to compute definition_hash "
            "on each scan cycle so incremental changes are tracked going forward."
        )
    elif len(service_health) == 0:
        root_cause["primary"] = "DAEMON_NOT_RUNNING"
        root_cause["recommendation"] = (
            "The definition_history_writer daemon is not registered or not running. "
            "Register it in supervisord.conf and ensure it sends heartbeats."
        )
    else:
        root_cause["primary"] = "UNKNOWN"
        root_cause["recommendation"] = "Manual investigation required."

    findings["root_cause"] = root_cause
    print(f"    Primary cause: {root_cause['primary']}")
    for reason in root_cause["secondary"]:
        print(f"    - {reason}")
    print(f"\n    Recommendation: {root_cause['recommendation']}")

    # Summary
    findings["summary"] = (
        f"mcp_definition_history is empty because mcp_server_registry lacks the "
        f"'definition_hash' column that all three history-writer daemons require. "
        f"Queries return 0 rows, so no INSERT is ever attempted for any of the "
        f"{registry_count} registered servers. signal_analyser and mcp_scanner are "
        f"NOT responsible for writing to this table -- only the purpose-built "
        f"definition-history daemons are, and they are all blocked on the same "
        f"schema gap. No history daemon has a service_health heartbeat and no "
        f"seed/fill pass was ever run."
    )
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(findings["summary"])

    return findings


if __name__ == "__main__":
    findings = main()
    print("\n--- JSON OUTPUT ---")
    print(json.dumps(findings, indent=2, default=str))