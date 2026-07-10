# deps: requests
"""Standalone daemon that monitors score deltas and writes perspective events.

It periodically queries the `mcp_llm_axis_scores` table for new rows (scored_at > last
run), detects changes in `p_top` greater than a threshold, and inserts rows into the
`perspective_events` table with ``change_type='score_delta'``.

The daemon is idempotent: it stores the timestamp of the last processed row in a
`.last_run` file and skips work if there is nothing new.

A self‑test is provided in the ``__main__`` block. It monkey‑patches ``requests.post``
to return synthetic data and asserts that exactly two events are emitted.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Any

import requests

# Configuration constants
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"
EXECUTE_ENDPOINT = f"{WRITE_SERVICE_URL}/execute"
HEARTBEAT_ENDPOINT = EXECUTE_ENDPOINT
HEARTBEAT_INTERVAL = 60  # seconds
POLL_INTERVAL = 300      # seconds
THRESHOLD = 5.0          # absolute delta on p_top
LAST_RUN_FILE = os.path.join(os.path.dirname(__file__), ".last_run")

# In‑memory cache of the last known p_top per server
_prev_p_top: Dict[str, float] = {}


def _read_last_run() -> datetime:
    """Read the timestamp of the last processed row.

    Returns a timezone‑aware ``datetime``. If the file does not exist, returns the epoch.
    """
    if not os.path.exists(LAST_RUN_FILE):
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        txt = open(LAST_RUN_FILE, "r", encoding="utf-8").read().strip()
        return datetime.fromisoformat(txt)
    except Exception:
        # Corrupt file – start from epoch
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _write_last_run(ts: datetime) -> None:
    """Persist the newest ``scored_at`` timestamp.

    The timestamp is stored as an ISO‑8601 string with UTC timezone.
    """
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        f.write(ts.astimezone(timezone.utc).isoformat())


def _query_new_scores(since: datetime) -> List[Dict[str, Any]]:
    """Query ``mcp_llm_axis_scores`` for rows newer than ``since``.

    Returns a list of dicts with keys: ``server_id``, ``p_top``, ``scored_at``.
    """
    sql = (
        "SELECT server_id, p_top, scored_at FROM mcp_llm_axis_scores "
        "WHERE scored_at > :since"
    )
    payload = {"sql": sql, "params": [since.isoformat()]}
    resp = requests.post(QUERY_ENDPOINT, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    # The service returns ``rows`` key according to the spec
    return data.get("rows", [])


def _insert_perspective_events(events: List[Dict[str, Any]]) -> None:
    """Insert rows into ``perspective_events``.

    ``events`` is a list of dicts matching the table columns.
    """
    if not events:
        return
    payload = {"table": "perspective_events", "rows": events, "wait": True}
    resp = requests.post(EXECUTE_ENDPOINT, json=payload, timeout=10)
    resp.raise_for_status()


def _heartbeat() -> None:
    """Send a heartbeat record to ``service_health``.
    """
    payload = {
        "table": "service_health",
        "rows": [{"status": "alive", "meta": json.dumps({"module": "scoring_delta_notifier"})}],
        "wait": True,
    }
    try:
        resp = requests.post(HEARTBEAT_ENDPOINT, json=payload, timeout=5)
        resp.raise_for_status()
    except Exception:
        # Heartbeat failures must not stop the daemon
        pass


def _process_cycle() -> None:
    """Perform a single poll‑process‑write cycle.
    """
    last_run = _read_last_run()
    rows = _query_new_scores(last_run)
    if not rows:
        return
    # Convert timestamps and sort to find the newest
    for r in rows:
        # Ensure ``scored_at`` is a datetime object
        if isinstance(r.get("scored_at"), str):
            r["scored_at"] = datetime.fromisoformat(r["scored_at"]).replace(tzinfo=timezone.utc)
    rows.sort(key=lambda x: x["scored_at"])
    newest_ts = rows[-1]["scored_at"]

    events_to_insert: List[Dict[str, Any]] = []
    for row in rows:
        server_id = row["server_id"]
        p_top = row.get("p_top")
        if p_top is None:
            continue
        prev = _prev_p_top.get(server_id, 0.0)
        delta = abs(p_top - prev)
        if delta > THRESHOLD:
            events_to_insert.append({
                "perspective_id": f"delta-{server_id}",
                "server_id": server_id,
                "change_type": "score_delta",
                "old_tier": None,
                "new_tier": None,
                "seen": False,
            })
        _prev_p_top[server_id] = p_top
    _insert_perspective_events(events_to_insert)
    _write_last_run(newest_ts)


def run() -> None:
    """Main daemon loop.
    """
    next_heartbeat = time.time() + HEARTBEAT_INTERVAL
    while True:
        _process_cycle()
        now = time.time()
        if now >= next_heartbeat:
            _heartbeat()
            next_heartbeat = now + HEARTBEAT_INTERVAL
        time.sleep(POLL_INTERVAL)

# ---------------------------------------------------------------------------
# Self‑test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Simple self‑test that patches ``requests.post``.
    import sys
    from types import SimpleNamespace

    class MockResponse:
        def __init__(self, json_data=None):
            self._json = json_data or {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    # Record calls for verification
    calls: List[Dict[str, Any]] = []

    def mock_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/query"):
            # Return three rows: two with p_top > 5, one <= 5
            rows = [
                {"server_id": "srv1", "p_top": 10.0, "scored_at": "2026-07-10T10:00:00+00:00"},
                {"server_id": "srv2", "p_top": 7.5, "scored_at": "2026-07-10T10:01:00+00:00"},
                {"server_id": "srv3", "p_top": 3.0, "scored_at": "2026-07-10T10:02:00+00:00"},
            ]
            return MockResponse({"rows": rows})
        elif url.endswith("/execute"):
            # Assume insert succeeded
            return MockResponse({})
        else:
            return MockResponse({})

    # Patch
    real_post = requests.post
    requests.post = mock_post
    try:
        # Ensure a clean state
        if os.path.exists(LAST_RUN_FILE):
            os.remove(LAST_RUN_FILE)
        _prev_p_top.clear()
        _process_cycle()
        # Verify that exactly two events were inserted
        insert_calls = [c for c in calls if c["url"].endswith("/execute")]
        if len(insert_calls) != 1:
            print("FAIL: expected one execute call, got", len(insert_calls))
            sys.exit(1)
        inserted_rows = insert_calls[0]["json"].get("rows", [])
        if len(inserted_rows) != 2:
            print("FAIL: expected 2 events, got", len(inserted_rows))
            sys.exit(1)
        print("PASS")
    finally:
        # Restore original function
        requests.post = real_post
