# deps: requests
"""Verification script for mcp_definition_history_populator execution.

Checks that the daemon is healthy via the service_health table and that the
mcp_definition_history table contains rows, including recent entries (last 24h).

Usage: python3 verify_mcp_definition_history_populator_execution.py
"""

import sys
import json
import datetime
from typing import Tuple

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def query(sql: str, params: list = None) -> Tuple[bool, any]:
    """Execute a query via write_service and return (success, result).
    On success, result is the JSON response's 'rows' field.
    """
    payload = {"sql": sql, "params": params or []}
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return True, data.get("rows", [])
    except Exception as e:
        return False, str(e)


def check_service_health() -> Tuple[bool, str]:
    """Verify that the mcp_definition_history_populator daemon is healthy.
    Returns (True, "") if healthy, else (False, error_message).
    """
    # The service_health table is expected to have columns: status, meta, timestamp.
    # We look for rows where meta contains the daemon name.
    sql = "SELECT status, meta FROM service_health WHERE meta LIKE ?"
    like_pattern = "%mcp_definition_history_populator%"
    ok, rows = query(sql, [like_pattern])
    if not ok:
        return False, f"service_health query failed: {rows}"
    if not rows:
        return False, "No service_health entry for mcp_definition_history_populator daemon"
    # Consider the daemon healthy if any row has status in a set of healthy values.
    healthy_statuses = {"healthy", "ok", "running", "up"}
    for row in rows:
        status = str(row.get("status", "")).lower()
        if status in healthy_statuses:
            return True, ""
    return False, f"Daemon found but status not healthy: {[r.get('status') for r in rows]}"


def check_history_populated() -> Tuple[bool, str]:
    """Verify that mcp_definition_history has rows and recent entries.
    Returns (True, "") if checks pass, else (False, error_message).
    """
    # 1) Total row count > 0
    ok, rows = query("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    if not ok:
        return False, f"history count query failed: {rows}"
    total_cnt = rows[0].get("cnt") if rows else None
    if not total_cnt or total_cnt <= 0:
        return False, f"mcp_definition_history table empty (count={total_cnt})"

    # 2) Recent entries within last 24 hours
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    # Assuming the table has a column named 'timestamp' storing ISO8601 strings.
    sql_recent = "SELECT COUNT(*) as recent_cnt FROM mcp_definition_history WHERE timestamp >= ?"
    ok, rows = query(sql_recent, [cutoff.isoformat()])
    if not ok:
        return False, f"recent entries query failed: {rows}"
    recent_cnt = rows[0].get("recent_cnt") if rows else None
    if not recent_cnt or recent_cnt <= 0:
        return False, f"No recent entries in last 24h (recent_cnt={recent_cnt})"

    return True, ""


def main() -> int:
    healthy, health_msg = check_service_health()
    if not healthy:
        print(f"FAIL: Service health check failed - {health_msg}")
        return 1

    populated, pop_msg = check_history_populated()
    if not populated:
        print(f"FAIL: History table check failed - {pop_msg}")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
