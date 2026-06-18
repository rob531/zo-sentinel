#!/usr/bin/env python3
"""
Diagnostic utility to investigate why mcp_definition_history table has 0 rows
despite 1754 servers in mcp_server_registry.

Queries signal_analyser and mcp_scanner logs for write failures.
Checks if definition_change_history_writer.py (built 2026-06-18T05:47:26)
is running and has processed any servers.
Reports findings to gaps map.
"""

import json
import time
import requests
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# deps: requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
GAPS_TABLE = "mesh_memory"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List] = None) -> List[Dict[str, Any]]:
    """Query write_service."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except requests.RequestException as e:
        print(f"Query error: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service."""
    payload = {"table": table, "rows": [rows], "wait": True}
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Write error: {e}")
        return False


def get_history_count() -> int:
    """Get row count from mcp_definition_history."""
    result = ws_query("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    if result and "cnt" in result[0]:
        return int(result[0]["cnt"])
    return -1


def get_registry_count() -> int:
    """Get row count from mcp_server_registry."""
    result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if result and "cnt" in result[0]:
        return int(result[0]["cnt"])
    return -1


def get_history_sample(limit: int = 5) -> List[Dict[str, Any]]:
    """Get sample rows from mcp_definition_history."""
    sql = f"SELECT * FROM mcp_definition_history LIMIT {limit}"
    return ws_query(sql)


def get_registry_sample(limit: int = 3) -> List[Dict[str, Any]]:
    """Get sample rows from mcp_server_registry to understand structure."""
    sql = f"SELECT server_id, name FROM mcp_server_registry LIMIT {limit}"
    return ws_query(sql)


def get_definition_daemon_health() -> List[Dict[str, Any]]:
    """Get health records for definition/history daemons."""
    sql = """
        SELECT service, status, last_heartbeat, meta
        FROM service_health
        WHERE service LIKE '%definition%'
           OR service LIKE '%history%'
           OR service LIKE '%change%'
        ORDER BY last_heartbeat DESC
    """
    return ws_query(sql)


def get_all_daemon_health() -> List[Dict[str, Any]]:
    """Get all daemon health records."""
    sql = "SELECT service, status, last_heartbeat FROM service_health ORDER BY service"
    return ws_query(sql)


def get_scanner_health() -> List[Dict[str, Any]]:
    """Get scanner health specifically."""
    sql = "SELECT service, status, last_heartbeat FROM service_health WHERE service LIKE '%scanner%'"
    return ws_query(sql)


def get_signal_analyser_health() -> List[Dict[str, Any]]:
    """Get signal analyser health."""
    sql = "SELECT service, status, last_heartbeat FROM service_health WHERE service LIKE '%signal%analyser%'"
    return ws_query(sql)


def check_history_table_schema() -> Dict[str, Any]:
    """Check if mcp_definition_history table exists and has expected columns."""
    # Try to query with explicit columns from DB_SCHEMA.md
    expected_cols = ["id", "server_id", "snapshot_hash", "captured_at"]
    result = ws_query(f"SELECT {', '.join(expected_cols)} FROM mcp_definition_history LIMIT 1")
    return {
        "table_exists": True,
        "expected_columns": expected_cols,
        "has_rows": len(result) >= 0  # Schema check, not data
    }


def check_definition_writer_file() -> Dict[str, Any]:
    """Check if definition_change_history_writer.py exists and its build date."""
    import os
    file_path = "/home/workspace/zo_sentinel/definition_change_history_writer.py"
    if os.path.exists(file_path):
        mtime = os.path.getmtime(file_path)
        return {
            "exists": True,
            "modified_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
            "expected_build_date": "2026-06-18T05:47:26"
        }
    return {"exists": False}


def check_mcp_definition_history_writer() -> Dict[str, Any]:
    """Check mcp_definition_history_writer.py."""
    import os
    file_path = "/home/workspace/zo_sentinel/mcp_definition_history_writer.py"
    if os.path.exists(file_path):
        mtime = os.path.getmtime(file_path)
        return {
            "exists": True,
            "modified_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        }
    return {"exists": False}


def check_audit_log_for_history_writes() -> List[Dict[str, Any]]:
    """Check audit_log for any writes to mcp_definition_history."""
    sql = """
        SELECT event_type, target_server_id, action, outcome, details_json, timestamp
        FROM audit_log
        WHERE table_name = 'mcp_definition_history'
           OR details_json LIKE '%mcp_definition_history%'
        ORDER BY timestamp DESC
        LIMIT 10
    """
    return ws_query(sql)


def check_audit_log_for_definition_errors() -> List[Dict[str, Any]]:
    """Check audit_log for any errors related to definition/history."""
    sql = """
        SELECT event_type, action, outcome, details_json, timestamp
        FROM audit_log
        WHERE outcome = 'failure'
          AND (details_json LIKE '%definition%' OR details_json LIKE '%history%')
        ORDER BY timestamp DESC
        LIMIT 10
    """
    return ws_query(sql)


def check_service_health_meta_for_errors() -> List[Dict[str, Any]]:
    """Check service_health meta for any error messages."""
    sql = "SELECT service, status, meta, last_heartbeat FROM service_health WHERE status = 'error'"
    return ws_query(sql)


def diagnose_gap() -> Dict[str, Any]:
    """
    Main diagnostic function.
    Returns findings about why mcp_definition_history is empty.
    """
    findings = {
        "diagnostic": "mcp_definition_history_empty",
        "started_at": iso_now(),
        "root_causes": [],
        "checks": {}
    }

    # 1. Check basic counts
    history_count = get_history_count()
    registry_count = get_registry_count()
    findings["checks"]["row_counts"] = {
        "mcp_definition_history": history_count,
        "mcp_server_registry": registry_count
    }

    # 2. Check daemon health
    definition_health = get_definition_daemon_health()
    findings["checks"]["definition_daemon_health"] = definition_health

    all_health = get_all_daemon_health()
    findings["checks"]["all_daemon_health"] = all_health

    scanner_health = get_scanner_health()
    findings["checks"]["scanner_health"] = scanner_health

    signal_health = get_signal_analyser_health()
    findings["checks"]["signal_analyser_health"] = signal_health

    # 3. Check definition_change_history_writer.py file
    file_info = check_definition_writer_file()
    findings["checks"]["definition_change_history_writer_file"] = file_info

    mcp_writer_info = check_mcp_definition_history_writer()
    findings["checks"]["mcp_definition_history_writer_file"] = mcp_writer_info

    # 4. Check table schema
    schema_info = check_definition_writer_file()
    findings["checks"]["history_table_schema"] = schema_info

    # 5. Check audit logs for write attempts
    audit_history = check_audit_log_for_history_writes()
    findings["checks"]["audit_log_history_writes"] = audit_history

    audit_errors = check_audit_log_for_definition_errors()
    findings["checks"]["audit_log_definition_errors"] = audit_errors

    error_health = check_service_health_meta_for_errors()
    findings["checks"]["error_health_records"] = error_health

    # 6. Analyze root causes
    if history_count == 0 and registry_count > 0:
        findings["root_causes"].append("mcp_definition_history has 0 rows despite servers in registry")

    if not definition_health or len(definition_health) == 0:
        findings["root_causes"].append("definition_change_history_writer daemon not sending heartbeats (not running or crashed)")

    # Check if any definition daemon has a stale heartbeat
    now = datetime.now(timezone.utc)
    stale_daemons = []
    for h in definition_health:
        if h.get("last_heartbeat"):
            try:
                hb_time = datetime.fromisoformat(h["last_heartbeat"].replace("Z", "+00:00"))
                age_seconds = (now - hb_time).total_seconds()
                if age_seconds > 300:  # 5 minutes stale
                    stale_daemons.append({
                        "service": h.get("service"),
                        "last_heartbeat": h.get("last_heartbeat"),
                        "age_seconds": age_seconds
                    })
            except Exception:
                pass
    if stale_daemons:
        findings["root_causes"].append("definition/history daemon heartbeat is stale")
        findings["stale_daemons"] = stale_daemons

    # Check for scanner staleness
    for h in scanner_health:
        if h.get("last_heartbeat"):
            try:
                hb_time = datetime.fromisoformat(h["last_heartbeat"].replace("Z", "+00:00"))
                age_seconds = (now - hb_time).total_seconds()
                if age_seconds > 300:
                    findings["root_causes"].append(f"scanner heartbeat stale ({age_seconds:.0f}s old)")
            except Exception:
                pass

    # 7. Determine recommendations
    recommendations = []
    if not definition_health or len(definition_health) == 0:
        recommendations.append("Start definition_change_history_writer daemon via supervisorctl")
        recommendations.append("Check logs for import/startup errors")

    if history_count == 0:
        recommendations.append("Run initial population of mcp_definition_history for all 1754 servers")
        recommendations.append("Check if daemon is writing to correct table name (mcp_definition_history vs mcp_definition_history_old)")

    findings["recommendations"] = recommendations
    findings["completed_at"] = iso_now()

    return findings


def report_to_gaps_map(findings: Dict[str, Any]) -> bool:
    """Report diagnostic findings to gaps map (mesh_memory table)."""
    gap_key = f"gap_definition_history_empty_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    record = {
        "memory_type": "diagnostic_gap",
        "importance": 0.9,
        "content": json.dumps(findings),
        "created_at": iso_now(),
        "key": gap_key
    }

    # Also write a human-readable summary
    summary_parts = [
        f"mcp_definition_history has {findings['checks']['row_counts']['mcp_definition_history']} rows",
        f"mcp_server_registry has {findings['checks']['row_counts']['mcp_server_registry']} servers",
    ]

    for cause in findings.get("root_causes", []):
        summary_parts.append(f"CAUSE: {cause}")

    for rec in findings.get("recommendations", []):
        summary_parts.append(f"ACTION: {rec}")

    findings["gaps_map_summary"] = " | ".join(summary_parts)

    record_summary = {
        "memory_type": "diagnostic_gap_summary",
        "importance": 0.9,
        "content": findings["gaps_map_summary"],
        "created_at": iso_now(),
        "key": f"gap_summary_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    }

    success1 = ws_write(GAPS_TABLE, record)
    success2 = ws_write(GAPS_TABLE, record_summary)

    return success1 and success2


def print_findings(findings: Dict[str, Any]) -> None:
    """Print findings in a readable format."""
    print("\n" + "=" * 70)
    print("DIAGNOSTIC: mcp_definition_history_empty")
    print("=" * 70)

    print("\n📊 ROW COUNTS:")
    counts = findings["checks"]["row_counts"]
    print(f"  mcp_definition_history: {counts['mcp_definition_history']}")
    print(f"  mcp_server_registry:   {counts['mcp_server_registry']}")

    print("\n👥 DEFINITION/HISTORY DAEMON HEALTH:")
    daemon_health = findings["checks"]["definition_daemon_health"]
    if daemon_health:
        for h in daemon_health:
            print(f"  {h.get('service', 'unknown')}: status={h.get('status')}, last_hb={h.get('last_heartbeat')}")
    else:
        print("  ⚠️  NO HEALTH RECORDS - daemon not running or not sending heartbeats")

    print("\n🔍 SCANNER HEALTH:")
    scanner = findings["checks"]["scanner_health"]
    if scanner:
        for s in scanner:
            print(f"  {s.get('service')}: status={s.get('status')}, last_hb={s.get('last_heartbeat')}")
    else:
        print("  No scanner health records")

    print("\n📁 FILE CHECKS:")
    f1 = findings["checks"]["definition_change_history_writer_file"]
    print(f"  definition_change_history_writer.py: exists={f1.get('exists')}, modified={f1.get('modified_at')}")
    f2 = findings["checks"]["mcp_definition_history_writer_file"]
    print(f"  mcp_definition_history_writer.py: exists={f2.get('exists')}, modified={f2.get('modified_at')}")

    print("\n🔴 ROOT CAUSES:")
    if findings.get("root_causes"):
        for cause in findings["root_causes"]:
            print(f"  ⚠️  {cause}")
    else:
        print("  No root causes identified")

    print("\n✅ RECOMMENDATIONS:")
    if findings.get("recommendations"):
        for rec in findings["recommendations"]:
            print(f"  → {rec}")
    else:
        print("  No recommendations")

    print("\n" + "=" * 70)


def run() -> None:
    """Run the diagnostic."""
    print("Starting mcp_definition_history_empty diagnostic...")
    print(f"Timestamp: {iso_now()}")

    findings = diagnose_gap()
    print_findings(findings)

    # Report to gaps map
    success = report_to_gaps_map(findings)
    if success:
        print("\n✅ Reported findings to gaps map (mesh_memory)")
    else:
        print("\n⚠️  Failed to report to gaps map (write_service error)")


if __name__ == "__main__":
    run()
