#!/usr/bin/env python3
"""
populate_mcp_signal_enrichments_schema.py

One-time utility to bootstrap the mcp_signal_enrichments table with structurally
valid synthetic rows for MCPs that already exist in mcp_server_registry.

This is NOT a daemon -- it runs once and exits.  A second run is a no-op (idempotent).
"""

# deps: requests

import argparse
import json
import random
from datetime import datetime, timezone
from typing import Any

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
HTTP_TIMEOUT = 10.0
WRITE_TIMEOUT = 30.0

SIGNAL_TYPES = ("supply_chain_enrichment", "community_signal_enrichment")

# Seed random for reproducible diagnostics in dry-run mode
random.seed(42)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ws_query(sql: str) -> list[dict[str, Any]]:
    resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def ws_execute(sql: str, params: list[Any] | None = None) -> None:
    payload: dict[str, Any] = {"sql": sql, "wait": True}
    if params is not None:
        payload["params"] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=WRITE_TIMEOUT)
    resp.raise_for_status()


def fetch_servers(limit: int = 50) -> list[dict[str, Any]]:
    """Fetch server_id from mcp_server_registry (limit 50 per task spec)."""
    sql = f"SELECT server_id FROM mcp_server_registry LIMIT {limit}"
    return ws_query(sql)


def check_already_populated() -> bool:
    """Return True if mcp_signal_enrichments already has rows."""
    rows = ws_query("SELECT 1 FROM mcp_signal_enrichments LIMIT 1")
    return len(rows) > 0


def generate_synthetic_row(server_id: str, signal_type: str) -> dict[str, Any]:
    """Generate a structurally valid synthetic enrichment row.

    Columns per live DB schema:
      id          BIGINT    (auto)
      server_id   VARCHAR   (PK part)
      signal_type VARCHAR   (PK part)
      dimension   VARCHAR
      score       FLOAT
      evidence_blob JSON
      computed_at TIMESTAMP
      expires_at  TIMESTAMP

    evidence_blob JSON per signal invariant (PRODUCT_SPEC §3):
      {signal_type, confidence, evidence_blob: {source, server_id, method}}
    """
    score = round(random.uniform(40.0, 85.0), 2)
    confidence = round(random.uniform(0.6, 0.9), 2)
    evidence_blob = {
        "signal_type": signal_type,
        "confidence": confidence,
        "evidence_blob": {
            "source": "synthetic_bootstrap",
            "server_id": server_id,
            "method": "random_sampling",
        },
    }
    return {
        "server_id": server_id,
        "signal_type": signal_type,
        "dimension": signal_type,
        "score": score,
        "evidence_blob": json.dumps(evidence_blob),
        "computed_at": utc_now_iso(),
        "expires_at": None,
    }


def build_insert_sql(rows: list[dict[str, Any]]) -> str:
    """Build a multi-row INSERT with ON CONFLICT DO NOTHING.

    Uses positional `?` placeholders so every value is passed through params,
    never interpolated directly into SQL.
    """
    if not rows:
        return ""

    # Columns from live mcp_signal_enrichments schema (no id - auto; no confidence)
    cols = ["server_id", "signal_type", "dimension", "score", "evidence_blob", "computed_at", "expires_at"]
    placeholders = "(" + ", ".join(["?"] * len(cols)) + ")"
    values_clause = ",\n".join([placeholders] * len(rows))

    sql = f"INSERT INTO mcp_signal_enrichments ({', '.join(cols)}) VALUES\n{values_clause}\nON CONFLICT DO NOTHING"
    return sql


def collect_params(rows: list[dict[str, Any]]) -> list[Any]:
    """Flatten rows into a params list matching `?` placeholders in build_insert_sql."""
    cols = ["server_id", "signal_type", "dimension", "score", "evidence_blob", "computed_at", "expires_at"]
    params = []
    for row in rows:
        for c in cols:
            params.append(row[c])
    return params


def run(dry_run: bool = False) -> None:
    if check_already_populated():
        print("Already populated, skipping")
        return

    servers = fetch_servers(limit=50)
    if not servers:
        print("No servers found in mcp_server_registry")
        return

    rows = []
    for server in servers:
        server_id = server["server_id"]
        for signal_type in SIGNAL_TYPES:
            rows.append(generate_synthetic_row(server_id, signal_type))

    sql = build_insert_sql(rows)
    params = collect_params(rows)

    if dry_run:
        print(sql)
        return

    ws_execute(sql, params=params)
    print(f"Populated {len(rows)} rows in mcp_signal_enrichments")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap mcp_signal_enrichments table")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL without executing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
