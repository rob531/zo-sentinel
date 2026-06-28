# deps: requests
"""Verification script for the mcp_definition_history_populator_daemon.

It checks that the `mcp_definition_history` table has recent entries (within the
last hour) and that the daemon has reported a recent heartbeat in the
`service_health` table.

Both checks are performed via the write_service HTTP API; no direct DuckDB
access is used.
"""

import datetime
import json
import sys
from typing import Any, Dict, List

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _query(sql: str, params: List[Any] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT query via the write_service.

    Args:
        sql: Parameterised SQL statement.
        params: List of parameters for the query.

    Returns:
        List of rows as dictionaries.
    """
    payload = {"sql": sql, "params": params or []}
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    # The write_service returns rows under a top‑level key; convention is
    # ``rows``. Guard against unexpected structures.
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    # Fallback – assume the response itself is the rows list.
    return data  # type: ignore


def _has_recent_history(hours: int = 1) -> bool:
    """Return True if the mcp_definition_history table has entries newer than
    ``hours`` ago.
    """
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=hours)).isoformat()
    sql = "SELECT COUNT(*) AS cnt FROM mcp_definition_history WHERE timestamp >= ?"
    rows = _query(sql, [cutoff])
    if not rows:
        return False
    return rows[0].get("cnt", 0) > 0


def _daemon_heartbeat_recent(daemon_name: str = "mcp_definition_history_populator_daemon", hours: int = 1) -> bool:
    """Check that the daemon reported a heartbeat within the last ``hours``.
    The service_health table is expected to have columns ``target_server_id``,
    ``status``, ``timestamp`` and optional ``meta``.
    """
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=hours)).isoformat()
    sql = (
        "SELECT status FROM service_health "
        "WHERE target_server_id = ? AND timestamp >= ? "
        "ORDER BY timestamp DESC LIMIT 1"
    )
    rows = _query(sql, [daemon_name, cutoff])
    if not rows:
        return False
    # Consider any non‑error status as healthy; the spec only requires a recent
    # entry, but we treat ``healthy`` as the canonical good value.
    status = rows[0].get("status", "").lower()
    return status == "healthy" or status == "ok"


def main() -> None:
    if not _has_recent_history():
        raise AssertionError("No recent entries in mcp_definition_history table.")
    if not _daemon_heartbeat_recent():
        raise AssertionError("Daemon heartbeat missing or not healthy in service_health table.")
    print("PASS")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Print the error to stderr and exit with non‑zero status.
        print(f"Verification failed: {e}", file=sys.stderr)
        sys.exit(1)
