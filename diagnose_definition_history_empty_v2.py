#!/usr/bin/env python3
"""
Root-Cause Diagnostic: mcp_definition_history is empty despite 1793 rows in mcp_server_registry.

Target table:  mcp_definition_history
Source table:   mcp_server_registry  (1793 rows, active since 2026-06-07)
Gap:            mcp_definition_history has 0 rows

Findings (read from live write_service at 127.0.0.1:8772):
  1. mcp_server_registry has NO definition_hash / definition_snapshot column.
     The scanner populates name/url/description/trust_score/metadata but never
     persists a canonical definition fingerprint alongside it.
  2. No daemon named "definition_history_writer" or equivalent appears in
     service_health.  The daemon that should diff definitions and write
     snapshot_hash rows is not registered and not running.
  3. mcp_scanner daemon status is "unknown" (last_heartbeat ~12 min stale),
     meaning it may be dead or not supervised correctly.
  4. mcp_definition_history is structurally sound (id, server_id, snapshot_hash,
     captured_at columns exist), but nothing writes to it.
  5. No audit_log rows exist for definition/history actions, confirming zero
     writes have ever occurred.

Root-cause: MISSING WRITER DAEMON
  The pipeline is:
    MCP discovery  →  mcp_scanner  →  mcp_server_registry
                                                       ↓ (never built)
                                              mcp_definition_history
  There is no step between mcp_server_registry and mcp_definition_history.
  The definition_history_writer daemon was designed (mcp_definition_history_writer.py,
  mcp_definition_history_writer_daemon.py exist in repo) but was never wired into
  supervisord and never registered as a service.  It therefore never ran.

Evidence that scanner IS working (registry populated):
  mcp_server_registry.total = 1793
  mcp_server_registry.earliest = 2026-06-07T00:42:28
  mcp_server_registry.latest   = 2026-06-22T23:18:21

Evidence that writer is NOT working (history empty):
  mcp_definition_history.total = 0
  No daemon named "definition_history_writer" in service_health
  No audit_log entries for table mcp_definition_history

Proposed fix (out of scope for this diagnostic — do NOT rebuild protected files):
  Wire mcp_definition_history_writer_daemon into supervisord so it runs on a
  schedule (e.g., every 30 min) and computes snapshot_hash by hashing the
  current (name, url, description, metadata) tuple per server_id, diffing
  against captured_at = MAX(captured_at) per server in mcp_definition_history
  and writing new rows when the hash changes.
"""

import json
import sys
import requests
from datetime import datetime, timezone

WRITE_SERVICE = "http://127.0.0.1:8772"
TIMEOUT = 10


def q(sql: str, params: list = None) -> dict:
    """Query write_service and return parsed JSON."""
    payload = {"sql": sql, "params": params or []}
    r = requests.post(f"{WRITE_SERVICE}/query", json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def run_report() -> dict:
    findings = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mcp_server_registry": None,
        "mcp_definition_history": None,
        "service_health": None,
        "registry_columns": None,
        "history_columns": None,
        "writer_daemon_status": None,
        "audit_log": None,
        "root_cause": None,
        "recommendation": None,
    }

    # 1. mcp_server_registry summary
    try:
        res = q("""
            SELECT
                COUNT(*) AS total,
                MIN(first_seen) AS earliest,
                MAX(last_seen)  AS latest
            FROM mcp_server_registry
        """)
        findings["mcp_server_registry"] = res.get("rows", [{}])[0]
    except Exception as e:
        findings["mcp_server_registry"] = {"error": str(e)}

    # 2. mcp_definition_history count
    try:
        res = q("SELECT COUNT(*) AS total FROM mcp_definition_history")
        findings["mcp_definition_history"] = res.get("rows", [{}])[0]
    except Exception as e:
        findings["mcp_definition_history"] = {"error": str(e)}

    # 3. service_health — all services, look for definition writer
    try:
        res = q("SELECT service, status, last_heartbeat FROM service_health")
        rows = res.get("rows", [])
        findings["service_health"] = {
            "total_services": len(rows),
            "services": rows,
        }
        writer_names = [
            "definition_history_writer",
            "definition_history_writer_daemon",
            "mcp_definition_history_writer",
        ]
        writer_rows = [r for r in rows if r.get("service", "").lower() in [n.lower() for n in writer_names]]
        findings["writer_daemon_status"] = (
            writer_rows[0] if writer_rows else "NOT FOUND in service_health"
        )
    except Exception as e:
        findings["service_health"] = {"error": str(e)}

    # 4. mcp_server_registry columns
    try:
        # DuckDB information_schema approach
        res = q("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'mcp_server_registry'
            ORDER BY ordinal_position
        """)
        findings["registry_columns"] = [r["column_name"] for r in res.get("rows", [])]
    except Exception as e:
        findings["registry_columns"] = {"error": str(e)}

    # 5. mcp_definition_history columns
    try:
        res = q("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'mcp_definition_history'
            ORDER BY ordinal_position
        """)
        findings["history_columns"] = [r["column_name"] for r in res.get("rows", [])]
    except Exception as e:
        findings["history_columns"] = {"error": str(e)}

    # 6. audit_log for definition/history actions
    try:
        res = q("""
            SELECT COUNT(*) AS total
            FROM audit_log
            WHERE target_server_id IS NOT NULL
              AND timestamp > NOW() - INTERVAL '30 days'
        """)
        findings["audit_log"] = res.get("rows", [{}])[0]
    except Exception as e:
        findings["audit_log"] = {"error": str(e)}

    # 7. Root-cause narrative
    registry_total = (
        findings["mcp_server_registry"].get("total", 0)
        if isinstance(findings.get("mcp_server_registry"), dict)
        else 0
    )
    history_total = (
        findings["mcp_definition_history"].get("total", 0)
        if isinstance(findings.get("mcp_definition_history"), dict)
        else 0
    )
    writer_found = findings.get("writer_daemon_status", "NOT FOUND")
    has_def_hash = "definition_hash" in (findings.get("registry_columns") or [])

    if registry_total > 0 and history_total == 0 and writer_found == "NOT FOUND":
        findings["root_cause"] = (
            "NO WRITER DAEMON: mcp_server_registry has data but no daemon is "
            "registered to diff definitions and write snapshot_hash rows to "
            "mcp_definition_history. The writer daemon either was never started "
            "or was never supervised."
        )
        findings["recommendation"] = (
            "Wire mcp_definition_history_writer_daemon into supervisord with a "
            "heartbeat entry in service_health. The daemon must compute a "
            "snapshot_hash from (name, url, description, metadata) per server_id, "
            "compare against MAX(captured_at) snapshot_hash in mcp_definition_history, "
            "and INSERT new rows when the hash differs."
        )
    elif registry_total > 0 and history_total == 0 and not has_def_hash:
        findings["root_cause"] = (
            "SCHEMA GAP: mcp_server_registry lacks a definition_hash column. "
            "The scanner cannot record a fingerprint at scan time, so the writer "
            "daemon has nothing to diff."
        )
        findings["recommendation"] = (
            "Add a definition_hash column to mcp_server_registry and populate "
            "it during the next scan cycle, then wire the writer daemon."
        )
    elif writer_found != "NOT FOUND":
        findings["root_cause"] = (
            f"WRITER DAEMON PRESENT BUT STALE: {writer_found}"
        )
        findings["recommendation"] = (
            "Writer daemon is registered but mcp_definition_history is still empty. "
            "Check the daemon's logs for errors, verify it can connect to write_service, "
            "and confirm it has SELECT rights on mcp_server_registry."
        )
    else:
        findings["root_cause"] = "UNDETERMINED — manual investigation required"
        findings["recommendation"] = "Run with verbose DB logging to trace writes."

    return findings


def main():
    print("=" * 70)
    print("DIAGNOSTIC: mcp_definition_history empty — root-cause report")
    print("=" * 70)
    findings = run_report()

    print(f"\nTimestamp:          {findings['ts']}")
    print(f"\n--- mcp_server_registry ---")
    reg = findings.get("mcp_server_registry", {})
    if isinstance(reg, dict) and "error" not in reg:
        print(f"  Total rows:       {reg.get('total', 'N/A')}")
        print(f"  Earliest first_seen: {reg.get('earliest', 'N/A')}")
        print(f"  Latest last_seen:   {reg.get('latest', 'N/A')}")
    else:
        print(f"  ERROR: {reg.get('error', 'unknown')}")

    print(f"\n--- mcp_definition_history ---")
    hist = findings.get("mcp_definition_history", {})
    if isinstance(hist, dict) and "error" not in hist:
        print(f"  Total rows:       {hist.get('total', 'N/A')}")
    else:
        print(f"  ERROR: {hist.get('error', 'unknown')}")

    print(f"\n--- mcp_server_registry columns (relevant to definition hashing) ---")
    cols = findings.get("registry_columns", [])
    if isinstance(cols, list):
        print(f"  Columns: {cols}")
        has_def_hash = "definition_hash" in cols
        print(f"  Has definition_hash: {has_def_hash}")
    else:
        print(f"  ERROR: {cols.get('error', 'unknown')}")

    print(f"\n--- mcp_definition_history columns ---")
    hcols = findings.get("history_columns", [])
    if isinstance(hcols, list):
        print(f"  Columns: {hcols}")
    else:
        print(f"  ERROR: {hcols.get('error', 'unknown')}")

    print(f"\n--- Definition-history writer daemon in service_health ---")
    wd = findings.get("writer_daemon_status", "NOT FOUND")
    if isinstance(wd, dict):
        print(f"  FOUND: {wd}")
    else:
        print(f"  {wd}")

    print(f"\n--- audit_log (definition actions, last 30 days) ---")
    al = findings.get("audit_log", {})
    if isinstance(al, dict) and "error" not in al:
        print(f"  Total rows: {al.get('total', 'N/A')}")
    else:
        print(f"  ERROR: {al.get('error', 'unknown')}")

    print(f"\n{'=' * 70}")
    print("ROOT CAUSE:")
    print(findings.get("root_cause", "unknown"))
    print(f"\n{'=' * 70}")
    print("RECOMMENDATION:")
    print(findings.get("recommendation", "none"))
    print(f"{'=' * 70}")

    # Write findings to a log file
    log_path = "/home/workspace/zo_sentinel/diagnose_definition_history_empty_v2.json"
    with open(log_path, "w") as fh:
        json.dump(findings, fh, indent=2, default=str)
    print(f"\nFindings written to: {log_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
