# deps: requests
"""diagnose_rug_pull_monitor_extreme_staleness.py

Utility script to diagnose extreme staleness of the ``rug_pull_monitor`` daemon.
It queries the ``service_health`` table via the ``write_service`` HTTP endpoint
to obtain the most recent heartbeat timestamp for the daemon and produces a
diagnostic dictionary.

Interface
---------
``run_diagnostic() -> dict``
    Returns a dictionary with the following keys:
    * ``status`` – ``'stale'`` if the last heartbeat is older than 24 hours,
      otherwise ``'healthy'``.
    * ``last_heartbeat_timestamp`` – ISO‑8601 string of the most recent
      heartbeat (or ``None`` if not found).
    * ``diagnosis_message`` – Human‑readable explanation.
"""

import json
import datetime
import requests
from typing import Optional, Dict

# Constants
_WRITE_SERVICE_URL = "http://127.0.0.1:8772"
_DAEMON_NAME = "rug_pull_monitor"
# Consider a daemon stale if no heartbeat within this many hours.
_STALE_THRESHOLD_HOURS = 24


def _query_last_heartbeat() -> Optional[str]:
    """Query the ``service_health`` table for the latest heartbeat timestamp.

    Returns the timestamp as an ISO‑8601 string, or ``None`` if the query
    returns no rows or an error occurs.
    """
    sql = (
        "SELECT timestamp FROM service_health "
        "WHERE target_server_id = ? "
        "ORDER BY timestamp DESC LIMIT 1"
    )
    payload = {
        "sql": sql,
        "params": [_DAEMON_NAME],
    }
    try:
        resp = requests.post(f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # Expected shape: {"rows": [{"timestamp": "..."}], "rowcount": 1}
        rows = data.get("rows", [])
        if rows:
            return rows[0].get("timestamp")
    except Exception as exc:
        # In a diagnostic script we prefer to fail gracefully.
        # Log to stdout for visibility; the caller will treat this as stale.
        print(f"[diagnostic] Failed to query service_health: {exc}")
    return None


def _hours_since(ts: str) -> float:
    """Calculate hours elapsed since the given ISO‑8601 timestamp.

    The timestamp is assumed to be UTC. If parsing fails, ``float('inf')`` is
    returned so the daemon is treated as stale.
    """
    try:
        dt = datetime.datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            # Assume UTC when timezone is missing.
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = now - dt
        return delta.total_seconds() / 3600.0
    except Exception:
        return float("inf")


def run_diagnostic() -> Dict[str, Optional[str]]:
    """Run the staleness diagnostic for ``rug_pull_monitor``.

    Returns a dictionary with ``status``, ``last_heartbeat_timestamp`` and
    ``diagnosis_message``.
    """
    ts = _query_last_heartbeat()
    if ts is None:
        status = "stale"
        message = (
            f"No heartbeat record found for daemon '{_DAEMON_NAME}'. "
            "Possible causes: process never started, immediate crash, or "
            "write_service not recording health."
        )
        return {
            "status": status,
            "last_heartbeat_timestamp": None,
            "diagnosis_message": message,
        }

    hours = _hours_since(ts)
    if hours > _STALE_THRESHOLD_HOURS:
        status = "stale"
        message = (
            f"Last heartbeat was {hours:.1f} hours ago (timestamp: {ts}). "
            "Potential causes include process termination, network outage, "
            "or internal error preventing heartbeat reporting."
        )
    else:
        status = "healthy"
        message = f"Daemon is healthy; last heartbeat {hours:.1f} hours ago (timestamp: {ts})."

    return {
        "status": status,
        "last_heartbeat_timestamp": ts,
        "diagnosis_message": message,
    }


if __name__ == "__main__":
    result = run_diagnostic()
    # Basic sanity checks.
    required_keys = {"status", "last_heartbeat_timestamp", "diagnosis_message"}
    assert required_keys.issubset(result.keys()), "Missing expected keys in diagnostic result"
    # Ensure the status is one of the expected values.
    assert result["status"] in {"stale", "healthy"}, "Unexpected status value"
    print("PASS")
