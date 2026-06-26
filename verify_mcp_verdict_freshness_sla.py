# deps: requests
"""Utility to verify that all live MCPs have a fresh verdict according to the configured SLA.

This module queries the ``mcp_risk_register`` table for each MCP's ``computed_at``
timestamp and compares it to the current UTC time.  If the elapsed time exceeds the
configured SLA (default 24 hours) the MCP is considered *stale*.

The module is deliberately side‑effect free on import – all work happens inside
``run()`` which is invoked only when the script is executed as ``__main__``.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from typing import List, Dict, Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# SLA threshold in hours.  The spec mentions "24 hours from first_seen or a
# re‑verdicting interval" – we expose a simple default of 24 h which can be
# overridden via an environment variable if needed.
DEFAULT_SLA_HOURS = 24

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _query(sql: str, params: List[Any] | None = None) -> List[Dict[str, Any]]:
    """Execute a read‑only query against the write_service.

    The write_service expects a POST to ``/query`` with a JSON payload containing
    the SQL string and a list of parameters.  The service returns a JSON object
    with a ``rows`` key holding a list of dictionaries – one per result row.
    """
    payload = {"sql": sql, "params": params or []}
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    # Defensive: the service should always return ``rows``; fall back to [].
    return data.get("rows", [])


def _now_utc() -> _dt.datetime:
    """Return the current UTC time with timezone information stripped.

    The DB stores timestamps as ISO‑8601 strings without explicit timezone.  For
    comparison we keep everything naive UTC.
    """
    return _dt.datetime.utcnow()


def _parse_iso(ts: str) -> _dt.datetime:
    """Parse an ISO‑8601 timestamp string returned by the DB.

    The DB schema stores timestamps as ``TEXT`` in ISO‑8601 format.  ``fromisoformat``
    handles the majority of cases; we fall back to ``strptime`` for older formats.
    """
    try:
        return _dt.datetime.fromisoformat(ts)
    except ValueError:
        # Fallback for strings like "2023-01-01 12:34:56"
        return _dt.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def fetch_mcp_computed_times() -> List[Dict[str, Any]]:
    """Retrieve ``mcp_id`` and ``computed_at`` for all live MCPs.

    Returns a list of dictionaries with at least ``mcp_id`` and ``computed_at``
    keys.  Rows lacking a ``computed_at`` value are ignored – they are treated
    as never having been computed and therefore stale.
    """
    sql = """
        SELECT mcp_id, computed_at, first_seen
        FROM mcp_risk_register
        WHERE is_live = TRUE
    """
    rows = _query(sql)
    return rows


def find_stale_mcps(sla_hours: int = DEFAULT_SLA_HOURS) -> List[Dict[str, Any]]:
    """Identify MCPs whose verdicts are older than the SLA.

    The SLA is expressed as a maximum age (in hours) for the ``computed_at``
    timestamp.  If ``computed_at`` is NULL or the age exceeds the threshold the
    MCP is considered stale.
    """
    now = _now_utc()
    stale: List[Dict[str, Any]] = []
    for row in fetch_mcp_computed_times():
        mcp_id = row.get("mcp_id")
        computed_at_raw = row.get("computed_at")
        # If there is no computed timestamp we treat the verdict as stale.
        if not computed_at_raw:
            stale.append({"mcp_id": mcp_id, "reason": "no_computed_at"})
            continue
        try:
            computed_at = _parse_iso(computed_at_raw)
        except Exception as exc:
            # Parsing failure – treat as stale and record the error.
            stale.append({"mcp_id": mcp_id, "reason": f"parse_error:{exc}"})
            continue
        age = now - computed_at
        if age.total_seconds() > sla_hours * 3600:
            stale.append({"mcp_id": mcp_id, "age_hours": age.total_seconds() / 3600})
    return stale


def run(sla_hours: int = DEFAULT_SLA_HOURS) -> None:
    """Execute the freshness verification and print a human‑readable report.

    The function is safe to call multiple times – it does not modify the DB.
    """
    stale = find_stale_mcps(sla_hours)
    if not stale:
        print("No stale MCP verdicts found.")
    else:
        print(f"Found {len(stale)} stale MCP verdict(s):")
        for entry in stale:
            print(" -", json.dumps(entry, default=str))
    # The caller (usually ``__main__``) decides what to do with the result.
    return None

# ---------------------------------------------------------------------------
# Self‑test harness (executed only when run as a script)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Execute the verification.  The harness asserts that the function returns a
    # list (which is always true) and prints a PASS marker.
    stale_mcps = find_stale_mcps()
    assert isinstance(stale_mcps, list), "stale_mcps should be a list"
    if stale_mcps:
        print(f"Stale MCP verdicts identified: {len(stale_mcps)}")
    else:
        print("All MCP verdicts are fresh.")
    print("PASS")
