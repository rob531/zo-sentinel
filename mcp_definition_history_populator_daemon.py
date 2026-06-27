# deps: requests
"""Daemon to populate the `mcp_definition_history` table.

Periodically scans `mcp_submissions` and `mcp_server_registry` for new or updated
MCP definitions and inserts them into `mcp_definition_history`. Ensures
idempotency – only new or changed definitions are inserted.

The daemon sends a heartbeat to the `service_health` table at least every 60
seconds.

All DB access goes through the write_service HTTP API on 127.0.0.1:8772.
"""

import json
import time
import threading
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import requests

# Constants
_WRITE_SERVICE_URL = "http://127.0.0.1:8772"
_HEARTBEAT_INTERVAL = 55  # seconds, less than the required 60
_POPULATE_INTERVAL = 30   # seconds between population cycles

# ---------------------------------------------------------------------------
# Helper DB access functions
# ---------------------------------------------------------------------------

def _query(sql: str, params: List = None) -> List[Dict]:
    """Execute a SELECT query via write_service.

    Args:
        sql: Parameterized SQL string.
        params: List of parameters for the query.

    Returns:
        List of rows as dictionaries.
    """
    payload = {
        "sql": sql,
        "params": params or []
    }
    resp = requests.post(f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def _write(table: str, rows: List[Dict]):
    """Insert rows into a table via write_service.

    Args:
        table: Target table name.
        rows: List of row dictionaries.
    """
    if not rows:
        return
    payload = {
        "table": table,
        "rows": rows,
        "wait": True
    }
    resp = requests.post(f"{_WRITE_SERVICE_URL}/write", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _heartbeat():
    """Write a heartbeat row to `service_health`.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    row = {
        "status": "alive",
        "meta": json.dumps({"daemon": "mcp_definition_history_populator"}),
        "timestamp": now_iso
    }
    _write("service_health", [row])

# ---------------------------------------------------------------------------
# Core population logic
# ---------------------------------------------------------------------------

def _fetch_submissions() -> List[Dict]:
    return _query("SELECT * FROM mcp_submissions", [])


def _fetch_server_registry() -> List[Dict]:
    return _query("SELECT * FROM mcp_server_registry", [])


def _fetch_existing_history() -> Dict[Tuple, Dict]:
    """Return a mapping keyed by (definition_id, source) to the stored row.
    """
    rows = _query("SELECT * FROM mcp_definition_history", [])
    mapping = {}
    for r in rows:
        key = (r.get("definition_id"), r.get("source"))
        mapping[key] = r
    return mapping


def _prepare_rows(submissions: List[Dict], registry: List[Dict]) -> List[Dict]:
    """Combine submissions and registry rows into a unified list for history.

    Each source gets a distinct `source` tag so we can track where the definition
    originated.
    """
    unified = []
    for s in submissions:
        unified.append({
            "definition_id": s.get("definition_id"),
            "definition": s.get("definition"),
            "source": "submission",
            "inserted_at": datetime.now(timezone.utc).isoformat()
        })
    for r in registry:
        unified.append({
            "definition_id": r.get("definition_id"),
            "definition": r.get("definition"),
            "source": "registry",
            "inserted_at": datetime.now(timezone.utc).isoformat()
        })
    return unified


def _filter_new_or_changed(unified: List[Dict], existing: Dict[Tuple, Dict]) -> List[Dict]:
    """Return rows that are not present or have a different definition.
    """
    to_insert = []
    for row in unified:
        key = (row["definition_id"], row["source"])  # unique per source
        existing_row = existing.get(key)
        if not existing_row:
            to_insert.append(row)
        else:
            # Compare definition content; if changed, insert a new version.
            if existing_row.get("definition") != row["definition"]:
                to_insert.append(row)
    return to_insert


def populate_once():
    """Perform a single population cycle and write a heartbeat.
    """
    submissions = _fetch_submissions()
    registry = _fetch_server_registry()
    unified = _prepare_rows(submissions, registry)
    existing = _fetch_existing_history()
    new_rows = _filter_new_or_changed(unified, existing)
    if new_rows:
        _write("mcp_definition_history", new_rows)
    _heartbeat()

# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def run(stop_event: threading.Event = None):
    """Main daemon loop.

    If `stop_event` is provided, the loop will exit when the event is set –
    useful for the test harness.
    """
    next_heartbeat = time.time()
    while True:
        populate_once()
        # Sleep until next populate interval
        time.sleep(_POPULATE_INTERVAL)
        # Heartbeat is already sent inside populate_once; we keep the interval
        # short enough that it satisfies the <=60s requirement.
        if stop_event and stop_event.is_set():
            break

# ---------------------------------------------------------------------------
# Self‑smoke / test harness
# ---------------------------------------------------------------------------

def _seed_test_data():
    """Insert deterministic test rows into `mcp_submissions` and `mcp_server_registry`.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    sub_rows = [
        {"definition_id": "def1", "definition": "{\"key\": \"value1\"}", "created_at": now_iso},
        {"definition_id": "def2", "definition": "{\"key\": \"value2\"}", "created_at": now_iso},
    ]
    reg_rows = [
        {"definition_id": "def3", "definition": "{\"key\": \"value3\"}", "created_at": now_iso},
    ]
    _write("mcp_submissions", sub_rows)
    _write("mcp_server_registry", reg_rows)


def _assert_population():
    rows = _query("SELECT definition_id, source FROM mcp_definition_history", [])
    ids = {(r["definition_id"], r["source"]) for r in rows}
    expected = {("def1", "submission"), ("def2", "submission"), ("def3", "registry")}
    assert expected.issubset(ids), f"Missing expected rows: {expected - ids}"

if __name__ == "__main__":
    # Seed test data, run daemon for two cycles, then verify idempotency.
    _seed_test_data()
    stop = threading.Event()
    daemon_thread = threading.Thread(target=run, args=(stop,))
    daemon_thread.start()
    # Allow two populate cycles (≈_POPULATE_INTERVAL * 2 + a little buffer)
    time.sleep(_POPULATE_INTERVAL * 2 + 5)
    stop.set()
    daemon_thread.join()
    # Verify that rows were inserted and that a second run didn't duplicate them.
    _assert_population()
    # Run populate_once again to confirm idempotency (no new rows should be added).
    before = len(_query("SELECT * FROM mcp_definition_history", []))
    populate_once()
    after = len(_query("SELECT * FROM mcp_definition_history", []))
    assert before == after, "Idempotency check failed: row count changed"
    print("PASS")
