# deps: requests
"""Utility script to verify the heartbeat of the `mcp_scanner` daemon.

It queries the `service_health` table via the write_service HTTP API and
checks whether the `last_heartbeat` timestamp for a given service is within a
freshness window (default 5 minutes).

The script is safe to import – all I/O happens inside functions or the
`__main__` block. The `__main__` block contains a self‑test that mocks the HTTP
responses for a healthy and a stale heartbeat and asserts that the correct
status messages are printed.
"""

import datetime as _dt
import json as _json
import sys as _sys
from typing import Tuple

import requests

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _query_service_health(service_name: str) -> str:
    """Query the `service_health` table for the `last_heartbeat` of *service_name*.

    Returns the timestamp as an ISO‑8601 string. Raises ``RuntimeError`` if the
    query fails or the service is not found.
    """
    payload = {
        "sql": "SELECT last_heartbeat FROM service_health WHERE service_name = ?",
        "params": [service_name],
    }
    try:
        resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=10)
    except Exception as exc:
        raise RuntimeError(f"Failed to contact write_service: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(f"write_service returned status {resp.status_code}: {resp.text}")

    data = resp.json()
    rows = data.get("rows", [])
    if not rows:
        raise RuntimeError(f"No health record found for service '{service_name}'.")
    # Expect exactly one row; take the first.
    ts = rows[0].get("last_heartbeat")
    if not ts:
        raise RuntimeError("'last_heartbeat' column missing in response.")
    return ts


def check_heartbeat(service_name: str, freshness_minutes: int = 5) -> Tuple[bool, str]:
    """Check whether *service_name* reported a recent heartbeat.

    Parameters
    ----------
    service_name: str
        Name of the service to check (e.g. ``"mcp_scanner"``).
    freshness_minutes: int, optional
        Maximum age of the heartbeat to be considered healthy.

    Returns
    -------
    (bool, str)
        ``True`` and a human‑readable message if the heartbeat is fresh, else
        ``False`` and a message describing the staleness.
    """
    ts_iso = _query_service_health(service_name)
    try:
        last_hb = _dt.datetime.fromisoformat(ts_iso)
    except Exception as exc:
        raise RuntimeError(f"Invalid timestamp format '{ts_iso}': {exc}") from exc

    now = _dt.datetime.utcnow()
    age = now - last_hb
    age_minutes = age.total_seconds() / 60
    if age_minutes <= freshness_minutes:
        msg = (
            f"Heartbeat for '{service_name}' is healthy: "
            f"last seen {age_minutes:.1f} minutes ago."
        )
        return True, msg
    else:
        msg = (
            f"Heartbeat for '{service_name}' is STALE: "
            f"last seen {age_minutes:.1f} minutes ago (>{freshness_minutes} min)."
        )
        return False, msg


# ---------------------------------------------------------------------------
# Self‑test harness
# ---------------------------------------------------------------------------
def _run_self_test() -> None:
    """Run a minimal self‑test by mocking ``requests.post``.

    Two scenarios are exercised:
    * a *healthy* heartbeat (2 minutes old)
    * a *stale* heartbeat (10 minutes old)
    The function prints the status messages and asserts that they contain the
    expected keywords. If both assertions succeed, ``PASS`` is printed.
    """
    from unittest.mock import patch

    # Helper to build a mock response object.
    class _MockResponse:
        def __init__(self, json_data, status_code=200):
            self._json = json_data
            self.status_code = status_code
            self.text = _json.dumps(json_data)

        def json(self):
            return self._json

    now = _dt.datetime.utcnow()
    healthy_ts = (now - _dt.timedelta(minutes=2)).isoformat()
    stale_ts = (now - _dt.timedelta(minutes=10)).isoformat()

    def _mock_post(url, json, timeout=None):  # pragma: no cover – exercised via patch
        sql = json.get("sql", "")
        if "SELECT last_heartbeat" not in sql:
            return _MockResponse({"rows": []}, status_code=400)
        # The service name is the first param.
        service = json.get("params", [""])[0]
        if service == "mcp_scanner":
            # Choose which timestamp to return based on a flag we set on the
            # mock object.
            ts = _mock_post.current_ts
            return _MockResponse({"rows": [{"last_heartbeat": ts}]})
        return _MockResponse({"rows": []}, status_code=404)

    # -------------------------------------------------------------------
    # Healthy scenario
    # -------------------------------------------------------------------
    _mock_post.current_ts = healthy_ts
    with patch.object(requests, "post", side_effect=_mock_post):
        healthy, msg = check_heartbeat("mcp_scanner")
        print(msg)
        assert healthy, "Expected heartbeat to be healthy"
        assert "healthy" in msg.lower()

    # -------------------------------------------------------------------
    # Stale scenario
    # -------------------------------------------------------------------
    _mock_post.current_ts = stale_ts
    with patch.object(requests, "post", side_effect=_mock_post):
        stale, msg = check_heartbeat("mcp_scanner")
        print(msg)
        assert not stale, "Expected heartbeat to be stale"
        assert "stale" in msg.lower()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()
