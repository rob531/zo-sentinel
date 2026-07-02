#!/usr/bin/env python3
"""
mcp_definition_history_backfill_script.py

One‑time utility that backfills the `mcp_definition_history` table with a snapshot
derived from the latest `mcp_llm_axis_scores` for each entry in
`mcp_server_registry`.

The script talks to a generic *write_service* via HTTP POST requests.  For the
purpose of the self‑test a very small in‑memory mock of that service is used.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

import requests

# --------------------------------------------------------------------------- #
# Configuration – endpoints of the write_service (can be overridden in tests)
# --------------------------------------------------------------------------- #
QUERY_URL = "http://write_service/query"
WRITE_URL = "http://write_service/write"


# --------------------------------------------------------------------------- #
# Helper functions that talk to the write_service
# --------------------------------------------------------------------------- #
def query_table(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
    order_by: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Query a table via the write_service.

    The request payload follows a tiny convention used by the mock service:
        {
            "action": "query",
            "table": "<table_name>",
            "filters": {...},          # optional
            "order_by": "<field>",     # optional, prefix '-' for DESC
            "limit": <int>             # optional
        }

    Returns a list of rows (each row is a dict).
    """
    payload = {
        "action": "query",
        "table": table,
        "filters": filters or {},
    }
    if order_by:
        payload["order_by"] = order_by
    if limit is not None:
        payload["limit"] = limit

    resp = requests.post(QUERY_URL, json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def insert_into_table(table: str, row: Dict[str, Any]) -> None:
    """
    Insert a single row into a table via the write_service.

    The request payload follows a tiny convention used by the mock service:
        {
            "action": "write",
            "table": "<table_name>",
            "data": {...}   # the row to insert
        }
    """
    payload = {"action": "write", "table": table, "data": row}
    resp = requests.post(WRITE_URL, json=payload)
    resp.raise_for_status()


# --------------------------------------------------------------------------- #
# Core back‑fill logic
# --------------------------------------------------------------------------- #
def backfill_definition_history() -> None:
    """
    For every server in `mcp_server_registry`:
        * fetch the latest `mcp_llm_axis_scores` (by `timestamp`)
        * build a snapshot row
        * insert it into `mcp_definition_history` if it does not already exist
    """
    # 1. Pull all server registry entries
    servers = query_table("mcp_server_registry")
    for server in servers:
        server_id = server["server_id"]

        # 2. Get the latest scores for this server
        scores = query_table(
            "mcp_llm_axis_scores",
            filters={"server_id": server_id},
            order_by="-timestamp",
            limit=1,
        )
        if not scores:
            # No scores for this server – nothing to back‑fill
            continue
        latest_score = scores[0]

        # 3. Build the snapshot row
        snapshot = {
            "server_id": server_id,
            "snapshot_timestamp": latest_score["timestamp"],
            # copy all server fields (except the primary key if any)
            **{k: v for k, v in server.items() if k != "server_id"},
            # copy all score fields (except server_id & timestamp which are already present)
            **{
                k: v
                for k, v in latest_score.items()
                if k not in ("server_id", "timestamp")
            },
        }

        # 4. Idempotency check – does a row with same server_id & snapshot_timestamp exist?
        existing = query_table(
            "mcp_definition_history",
            filters={
                "server_id": server_id,
                "snapshot_timestamp": snapshot["snapshot_timestamp"],
            },
        )
        if existing:
            # Already present – skip insertion
            continue

        # 5. Insert the snapshot
        insert_into_table("mcp_definition_history", snapshot)


# --------------------------------------------------------------------------- #
# Self‑test infrastructure (uses an in‑memory mock of the write_service)
# --------------------------------------------------------------------------- #
class _MockWriteService:
    """
    Very small in‑memory mock of the write_service used only for the self‑test.
    It stores tables as dicts of list‑of‑dict rows.
    """

    def __init__(self) -> None:
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "mcp_server_registry": [],
            "mcp_llm_axis_scores": [],
            "mcp_definition_history": [],
        }

    # ------------------------------------------------------------------- #
    # Emulated HTTP POST handler
    # ------------------------------------------------------------------- #
    def post(self, url: str, json: Dict[str, Any]) -> "MockResponse":
        action = json.get("action")
        if action == "query":
            return self._handle_query(json)
        if action == "write":
            return self._handle_write(json)
        raise ValueError(f"Unsupported mock action: {action}")

    # ------------------------------------------------------------------- #
    # Query handling
    # ------------------------------------------------------------------- #
    def _handle_query(self, payload: Dict[str, Any]) -> "MockResponse":
        table = payload["table"]
        rows = list(self.tables.get(table, []))  # copy

        # Apply filters
        filters = payload.get("filters", {})
        if filters:
            rows = [
                r
                for r in rows
                if all(r.get(k) == v for k, v in filters.items())
            ]

        # Apply ordering
        order_by = payload.get("order_by")
        if order_by:
            reverse = order_by.startswith("-")
            key = order_by.lstrip("-")
            rows.sort(key=lambda r: r.get(key), reverse=reverse)

        # Apply limit
        limit = payload.get("limit")
        if limit is not None:
            rows = rows[:limit]

        return MockResponse(200, {"data": rows})

    # ------------------------------------------------------------------- #
    # Write handling
    # ------------------------------------------------------------------- #
    def _handle_write(self, payload: Dict[str, Any]) -> "MockResponse":
        table = payload["table"]
        row = payload["data"]
        self.tables.setdefault(table, []).append(row)
        return MockResponse(200, {"status": "ok"})

    # ------------------------------------------------------------------- #
    # Helper to reset tables (used between test runs)
    # ------------------------------------------------------------------- #
    def reset(self) -> None:
        for tbl in self.tables:
            self.tables[tbl].clear()


class MockResponse:
    """Mimics ``requests.Response`` for the mock service."""

    def __init__(self, status_code: int, json_body: Dict[str, Any]) -> None:
        self.status_code = status_code
        self._json_body = json_body

    def raise_for_status(self) -> None:
        if not (200 <= self.status_code < 300):
            raise RuntimeError(f"HTTP error {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._json_body


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    """
    Entry point for the script.  When executed directly it runs a tiny self‑test
    that demonstrates the back‑fill logic.
    """
    # ------------------------------------------------------------------- #
    # Install the mock service (only for the self‑test)
    # ------------------------------------------------------------------- #
    mock_service = _MockWriteService()
    # Monkey‑patch ``requests.post`` so that the rest of the code talks to the mock.
    original_post = requests.post
    requests.post = mock_service.post  # type: ignore[assignment]

    try:
        # ------------------------------------------------------------------- #
        # 1️⃣  Populate mock tables with example data
        # ------------------------------------------------------------------- #
        # Two servers
        mock_service.tables["mcp_server_registry"].extend(
            [
                {"server_id": "srv-1", "name": "Alpha", "region": "us-east"},
                {"server_id": "srv-2", "name": "Beta", "region": "eu-west"},
            ]
        )
        # Scores – multiple per server, different timestamps
        mock_service.tables["mcp_llm_axis_scores"].extend(
            [
                # srv-1 scores
                {
                    "server_id": "srv-1",
                    "timestamp": "2023-01-01T10:00:00Z",
                    "accuracy": 0.85,
                    "latency": 120,
                },
                {
                    "server_id": "srv-1",
                    "timestamp": "2023-01-02T10:00:00Z",  # latest
                    "accuracy": 0.88,
                    "latency": 115,
                },
                # srv-2 scores
                {
                    "server_id": "srv-2",
                    "timestamp": "2023-01-01T11:00:00Z",
                    "accuracy": 0.80,
                    "latency": 130,
                },
                {
                    "server_id": "srv-2",
                    "timestamp": "2023-01-03T11:00:00Z",  # latest
                    "accuracy": 0.82,
                    "latency": 125,
                },
            ]
        )

        # ------------------------------------------------------------------- #
        # 2️⃣  Run the back‑fill logic
        # ------------------------------------------------------------------- #
        backfill_definition_history()

        # ------------------------------------------------------------------- #
        # 3️⃣  Verify that the history table contains exactly one row per server,
        #     each reflecting the *latest* scores.
        # ------------------------------------------------------------------- #
        history = mock_service.tables["mcp_definition_history"]
        assert len(history) == 2, f"expected 2 rows, got {len(history)}"

        # Helper to locate a row by server_id
        def find_row(sid: str) -> Dict[str, Any]:
            for r in history:
                if r["server_id"] == sid:
                    return r
            raise AssertionError(f"row for {sid} not found")

        row1 = find_row("srv-1")
        assert row1["snapshot_timestamp"] == "2023-01-02T10:00:00Z"
        assert row1["accuracy"] == 0.88
        assert row1["latency"] == 115
        assert row1["name"] == "Alpha"
        assert row1["region"] == "us-east"

        row2 = find_row("srv-2")
        assert row2["snapshot_timestamp"] == "2023-01-03T11:00:00Z"
        assert row2["accuracy"] == 0.82
        assert row2["latency"] == 125
        assert row2["name"] == "Beta"
        assert row2["region"] == "eu-west"

        # ------------------------------------------------------------------- #
        # 4️⃣  Idempotency check – running the back‑fill again must not add rows
        # ------------------------------------------------------------------- #
        backfill_definition_history()
        assert len(mock_service.tables["mcp_definition_history"]) == 2, (
            "idempotency failed – duplicate rows were created"
        )

        print("✅ Self‑test passed – back‑fill behaved as expected.")
    finally:
        # Restore the original ``requests.post`` in case this script is imported
        # elsewhere.
        requests.post = original_post  # type: ignore[assignment]


if __name__ == "__main__":
    main()