#!/usr/bin/env python3
"""
diagnose_definition_history_empty_gap_v6.py

Diagnostic utility to investigate why mcp_definition_history is empty (0 rows).

Investigates:
1. Live row counts for mcp_definition_history vs mcp_server_registry
2. Actual column schemas (queried live, not from static docs)
3. Whether mcp_scanner.py writes to mcp_definition_history
4. Whether signal_analyser.py writes to mcp_definition_history
5. Service health records for any daemons that should be writing to this table
6. audit_log for any historical write attempts to mcp_definition_history

Produces:
- Root cause determination: missing INSERT in existing daemon OR missing consumer daemon
- Recommended fix location with specific file/line guidance

This is a READ-ONLY diagnostic utility -- no DB writes, no side effects.
"""
# deps: requests

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
TIMEOUT = 10


def ts_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[list] = None) -> dict:
    """Execute a SELECT via write_service query endpoint."""
    import requests
    payload: dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "rows": []}


def live_columns(table: str) -> list[str]:
    """Get live column list for a table via information_schema."""
    result = ws_query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table]
    )
    if result.get("rows"):
        return [r["column_name"] for r in result["rows"] if "column_name" in r]
    return []


def scan_source_for_history_writes(filepath: str) -> dict:
    """Check if a source file writes to mcp_definition_history."""
    findings = {
        "path": filepath,
        "exists": os.path.exists(filepath),
        "writes_to_history": False,
        "imports_write_service": False,
        "has_ws_write_call": False,
        "writes_to_service_health": False,
        "relevant_lines": [],
    }
    if not findings["exists"]:
        return findings

    with open(filepath) as f:
        content = f.read()

    findings["writes_to_history"] = "mcp_definition_history" in content
    findings["imports_write_service"] = "write_service" in content.lower() or "8772" in content
    findings["has_ws_write_call"] = "ws_write" in content or "requests.post" in content
    findings["writes_to_service_health"] = "service_health" in content

    # Collect relevant code lines
    for lineno, line in enumerate(content.splitlines(), 1):
        if any(kw in line for kw in [
            "mcp_definition_history",
            "ws_write",
            "definition_history",
            "snapshot",
        ]):
            findings["relevant_lines"].append({
                "lineno": lineno,
                "snippet": line.strip()[:120],
            })

    return findings


def check_daemon_health() -> dict:
    """Query service_health for daemons that might write to mcp_definition_history."""
    result = ws_query(
        "SELECT service, status, last_heartbeat, meta "
        "FROM service_health "
        "WHERE service LIKE '%definition%' "
        "   OR service LIKE '%history%' "
        "   OR service LIKE '%change%' "
        "   OR service LIKE '%scanner%' "
        "   OR service LIKE '%analyser%' "
        "ORDER BY last_heartbeat DESC "
        "LIMIT 20"
    )
    rows = result.get("rows", [])
    return {
        "daemons_found": len(rows),
        "daemons": rows,
    }


def check_audit_log() -> dict:
    """Check audit_log for writes/failures targeting mcp_definition_history."""
    result = ws_query(
        "SELECT event_type, actor, target_server_id, action, outcome, "
        "       details_json, timestamp "
        "FROM audit_log "
        "WHERE details_json LIKE '%mcp_definition_history%' "
        "   OR action LIKE '%definition_history%' "
        "ORDER BY timestamp DESC "
        "LIMIT 10"
    )
    rows = result.get("rows", [])
    return {
        "write_attempts": len(rows),
        "attempts": rows,
    }


def main() -> dict:
    findings: dict[str, Any] = {
        "diagnostic": "diagnose_definition_history_empty_gap_v6",
        "timestamp": ts_now(),
        "target_table": "mcp_definition_history",
    }

    # 1. Row counts
    hist_count = ws_query("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    reg_count = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")

    hist_rows = hist_count.get("rows", [])
    reg_rows = reg_count.get("rows", [])

    hist_cnt = int(hist_rows[0].get("cnt", 0)) if hist_rows and "cnt" in hist_rows[0] else 0
    reg_cnt = int(reg_rows[0].get("cnt", 0)) if reg_rows and "cnt" in reg_rows[0] else 0

    findings["row_counts"] = {
        "mcp_definition_history": hist_cnt,
        "mcp_server_registry": reg_cnt,
        "gap_confirmed": hist_cnt == 0 and reg_cnt > 0,
    }

    # 2. Schema analysis
    hist_cols = live_columns("mcp_definition_history")
    reg_cols = live_columns("mcp_server_registry")

    findings["schemas"] = {
        "mcp_definition_history_columns": hist_cols,
        "mcp_server_registry_columns": reg_cols,
        "registry_has_definition_column": "definition" in reg_cols,
    }

    # 3. Scanner wiring analysis
    scanner_path = "/home/workspace/zo_sentinel/mcp_scanner.py"
    findings["mcp_scanner"] = scan_source_for_history_writes(scanner_path)

    # 4. Signal analyser wiring analysis
    analyser_path = "/home/workspace/zo_sentinel/signal_analyser.py"
    findings["signal_analyser"] = scan_source_for_history_writes(analyser_path)

    # 5. Daemon health
    findings["daemon_health"] = check_daemon_health()

    # 6. Audit log
    findings["audit_log"] = check_audit_log()

    # 7. Root cause determination
    root_causes: list[str] = []
    fix_type: str = "unknown"
    fix_location: str = ""

    if hist_cnt == 0 and reg_cnt > 0:
        root_causes.append("CONFIRMED: mcp_definition_history has 0 rows while mcp_server_registry has >0 rows")

    scanner_writes = findings["mcp_scanner"]["writes_to_history"]
    analyser_writes = findings["signal_analyser"]["writes_to_history"]
    daemon_health = findings["daemon_health"]["daemons_found"]

    if not scanner_writes and not analyser_writes:
        root_causes.append(
            "ROOT CAUSE TYPE 1: Neither mcp_scanner.py nor signal_analyser.py "
            "writes to mcp_definition_history. No INSERT statement exists in either daemon."
        )
        fix_type = "missing_insert_in_existing_daemon"
        fix_location = "mcp_scanner.py -- add ws_write('mcp_definition_history', ...) after upserting to mcp_server_registry"
    elif daemon_health == 0:
        root_causes.append(
            "ROOT CAUSE TYPE 2: No daemon is registered as writing to mcp_definition_history. "
            "Even if code exists, no consumer daemon is running."
        )
        fix_type = "missing_consumer_daemon"

    # Check for writer daemon files
    writer_files = [
        "definition_change_history_writer.py",
        "definition_change_history_writer_v2.py",
        "definition_change_monitor.py",
        "mcp_definition_history_writer.py",
    ]
    existing_writers = []
    for fname in writer_files:
        path = f"/home/workspace/zo_sentinel/{fname}"
        if os.path.exists(path):
            existing_writers.append(fname)

    if existing_writers:
        root_causes.append(
            f"WRITER DAEMON FILES EXIST but not registered in service_health: {existing_writers}. "
            "These may be broken/old versions or not started."
        )

    findings["existing_writer_daemon_files"] = existing_writers
    findings["root_cause"] = {
        "causes": root_causes,
        "classification": fix_type,
        "recommended_fix_location": fix_location,
    }

    # 8. Recommendations
    recommendations: list[str] = []

    if fix_type == "missing_insert_in_existing_daemon":
        recommendations.append(
            "FIX: Add snapshot write to mcp_scanner.py after each server upsert. "
            "Compute snapshot_hash from (server_id, name, url, description, metadata) "
            "and write (server_id, snapshot_hash, captured_at) to mcp_definition_history. "
            "Make it idempotent: skip if snapshot_hash matches the last captured row."
        )
    elif fix_type == "missing_consumer_daemon":
        recommendations.append(
            "FIX: Either start an existing writer daemon or create a new standalone "
            "snapshot consumer daemon that runs after mcp_scanner cycles."
        )

    findings["recommendations"] = recommendations

    return findings


def print_report(findings: dict) -> None:
    """Print human-readable diagnostic report."""
    print("\n" + "=" * 70)
    print("  mcp_definition_history EMPTY GAP DIAGNOSTIC  (v6)")
    print("=" * 70)
    print(f"  Timestamp: {findings['timestamp']}\n")

    # Row counts
    print("--- ROW COUNTS ---")
    rc = findings["row_counts"]
    print(f"  mcp_definition_history : {rc['mcp_definition_history']} rows")
    print(f"  mcp_server_registry    : {rc['mcp_server_registry']} rows")
    print(f"  Gap confirmed           : {rc['gap_confirmed']}")

    # Schemas
    print("\n--- TABLE SCHEMAS ---")
    schemas = findings["schemas"]
    print(f"  mcp_definition_history columns: {schemas['mcp_definition_history_columns']}")
    print(f"  mcp_server_registry columns   : {len(schemas['mcp_server_registry_columns'])} columns")

    # Scanner wiring
    print("\n--- MCP_SCANNER WIRING ---")
    sc = findings["mcp_scanner"]
    print(f"  File exists            : {sc['exists']}")
    print(f"  Writes to history      : {sc['writes_to_history']}")
    print(f"  Uses ws_write          : {sc['has_ws_write_call']}")
    if sc["relevant_lines"]:
        print("  Relevant lines:")
        for rl in sc["relevant_lines"][:5]:
            print(f"    {rl['lineno']}: {rl['snippet']}")

    # Analyser wiring
    print("\n--- SIGNAL_ANALYSER WIRING ---")
    sa = findings["signal_analyser"]
    print(f"  File exists            : {sa['exists']}")
    print(f"  Writes to history      : {sa['writes_to_history']}")
    print(f"  Uses ws_write          : {sa['has_ws_write_call']}")

    # Daemon health
    print("\n--- DAEMON HEALTH RECORDS ---")
    dh = findings["daemon_health"]
    print(f"  Daemons found          : {dh['daemons_found']}")
    for d in dh["daemons"]:
        print(f"    {d.get('service')}: status={d.get('status')}, "
              f"last_heartbeat={d.get('last_heartbeat')}")

    # Audit log
    print("\n--- AUDIT LOG WRITES ---")
    al = findings["audit_log"]
    print(f"  Write attempts found   : {al['write_attempts']}")

    # Writer daemon files
    print("\n--- EXISTING WRITER DAEMON FILES ---")
    for f in findings["existing_writer_daemon_files"]:
        print(f"  {f}")
    if not findings["existing_writer_daemon_files"]:
        print("  (none found)")

    # Root cause
    print("\n" + "-" * 70)
    print("  ROOT CAUSE DETERMINATION")
    print("-" * 70)
    rc = findings["root_cause"]
    for i, cause in enumerate(rc["causes"], 1):
        print(f"  {i}. {cause}")
    print(f"\n  Classification : {rc['classification']}")
    print(f"  Fix location   : {rc['recommended_fix_location']}")

    # Recommendations
    print("\n" + "-" * 70)
    print("  RECOMMENDATIONS")
    print("-" * 70)
    for rec in findings["recommendations"]:
        print(f"  • {rec}")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    findings = main()
    print_report(findings)

    # Also emit machine-readable JSON
    print("--- JSON OUTPUT ---")
    print(json.dumps(findings, indent=2, default=str))

    # Exit code: 0 if gap confirmed (diagnostic complete), 1 otherwise
    gap = findings["row_counts"]["gap_confirmed"]
    sys.exit(0 if gap else 0)  # Always 0 -- this is a diagnostic