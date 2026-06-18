#!/usr/bin/env python3
# deps: requests
"""
Diagnostic utility to investigate the 'Registry read error' reported in
current build state.

Investigates:
  1. write_service connectivity at :8772
  2. mcp_server_registry table integrity via information_schema.columns
  3. Recent daemon heartbeats from service_health table

Diagnostic-only module: no DB writes, no daemon registration.
Output: structured JSON to stdout for operator review.
"""
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_TIMEOUT = 10
DIAGNOSTIC_NAME = "diagnose_registry_read_error"


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write_service. Returns rows list or raises on error."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql},
        timeout=QUERY_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


def ws_execute(sql: str) -> dict[str, Any]:
    """Execute a DDL/DML statement via write_service. Returns result dict."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/execute",
        json={"sql": sql, "wait": True},
        timeout=QUERY_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------------
# Probe 1: write_service connectivity
# ------------------------------------------------------------------
def probe_write_service_connectivity() -> dict[str, Any]:
    """Ping write_service /query endpoint to verify it is responding."""
    started = time.monotonic()
    try:
        # Lightweight query that works even if DB is partially init'd
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT 1 AS alive"},
            timeout=QUERY_TIMEOUT,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "probe": "write_service_connectivity",
            "reachable": True,
            "status_code": resp.status_code,
            "elapsed_ms": elapsed_ms,
            "response_valid": resp.status_code == 200,
            "error": None,
        }
    except requests.exceptions.ConnectionError as e:
        return {
            "probe": "write_service_connectivity",
            "reachable": False,
            "status_code": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "response_valid": False,
            "error": f"ConnectionError: {e}",
        }
    except requests.exceptions.Timeout as e:
        return {
            "probe": "write_service_connectivity",
            "reachable": True,
            "status_code": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "response_valid": False,
            "error": f"Timeout: {e}",
        }
    except Exception as e:
        return {
            "probe": "write_service_connectivity",
            "reachable": False,
            "status_code": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "response_valid": False,
            "error": str(e),
        }


# ------------------------------------------------------------------
# Probe 2: mcp_server_registry table integrity
# ------------------------------------------------------------------
def probe_mcp_server_registry_integrity() -> dict[str, Any]:
    """
    Verify mcp_server_registry exists in information_schema and list its
    columns with types. Also check for a row count.
    """
    result: dict[str, Any] = {
        "probe": "mcp_server_registry_integrity",
        "table_exists": False,
        "columns": [],
        "row_count": None,
        "has_required_columns": False,
        "error": None,
    }

    # Check table presence and column definitions
    try:
        cols = ws_query("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'mcp_server_registry'
            ORDER BY ordinal_position
        """)
        result["table_exists"] = len(cols) > 0
        result["columns"] = cols

        # Required columns for a healthy registry
        required = {"server_id", "name", "description", "enabled"}
        present = {c["column_name"] for c in cols}
        result["has_required_columns"] = required.issubset(present)
        result["missing_required_columns"] = list(required - present)

    except Exception as e:
        result["error"] = str(e)
        return result

    # Row count
    if result["table_exists"]:
        try:
            rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_server_registry")
            result["row_count"] = rows[0]["cnt"] if rows else 0
        except Exception as e:
            result["row_count_error"] = str(e)

    return result


# ------------------------------------------------------------------
# Probe 3: Recent daemon heartbeats from service_health
# ------------------------------------------------------------------
def probe_service_health_recent(limit: int = 20) -> dict[str, Any]:
    """
    List the most recent entries in service_health ordered by ts DESC.
    """
    result: dict[str, Any] = {
        "probe": "service_health_recent",
        "table_exists": False,
        "entries": [],
        "error": None,
    }

    try:
        rows = ws_query(f"""
            SELECT service, status, ts, meta
            FROM service_health
            ORDER BY ts DESC
            LIMIT {limit}
        """)
        result["table_exists"] = True
        result["entries"] = rows
    except Exception as e:
        result["error"] = str(e)

    return result


# ------------------------------------------------------------------
# Probe 4: audit_log for recent registry errors
# ------------------------------------------------------------------
def probe_audit_log_registry_errors(limit: int = 10) -> dict[str, Any]:
    """
    Search audit_log for recent events mentioning 'registry' or 'read error'.
    """
    result: dict[str, Any] = {
        "probe": "audit_log_registry_errors",
        "table_exists": False,
        "entries": [],
        "error": None,
    }

    try:
        rows = ws_query(f"""
            SELECT event_id, event_type, action, outcome, details_json, timestamp
            FROM audit_log
            WHERE event_type ILIKE '%registry%'
               OR action ILIKE '%registry%'
               OR details_json ILIKE '%registry read%'
            ORDER BY timestamp DESC
            LIMIT {limit}
        """)
        result["table_exists"] = True
        result["entries"] = rows
    except Exception as e:
        result["error"] = str(e)

    return result


# ------------------------------------------------------------------
# Probe 5: mcp_signal_scores for server_id presence
# ------------------------------------------------------------------
def probe_mcp_signal_scores(server_id: str | None = None) -> dict[str, Any]:
    """
    Check if mcp_signal_scores references the registry and sample rows.
    """
    result: dict[str, Any] = {
        "probe": "mcp_signal_scores",
        "table_exists": False,
        "row_count": None,
        "sample": [],
        "error": None,
    }

    try:
        rows = ws_query("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'mcp_signal_scores'
        """)
        result["table_exists"] = len(rows) > 0

        if result["table_exists"]:
            count_rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_signal_scores")
            result["row_count"] = count_rows[0]["cnt"] if count_rows else 0

            sample_sql = "SELECT * FROM mcp_signal_scores LIMIT 3"
            if server_id:
                sample_sql = f"SELECT * FROM mcp_signal_scores WHERE server_id = '{server_id}' LIMIT 3"
            result["sample"] = ws_query(sample_sql)

    except Exception as e:
        result["error"] = str(e)

    return result


# ------------------------------------------------------------------
# Main run
# ------------------------------------------------------------------
def run() -> None:
    findings: dict[str, Any] = {
        "diagnostic": DIAGNOSTIC_NAME,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "write_service_url": WRITE_SERVICE_URL,
        "probes": {},
        "summary": {
            "write_service_reachable": False,
            "registry_table_healthy": False,
            "daemons_reporting": False,
        },
    }

    # Run probes
    probes = [
        ("connectivity", probe_write_service_connectivity),
        ("registry_integrity", probe_mcp_server_registry_integrity),
        ("service_health", probe_service_health_recent),
        ("audit_log", probe_audit_log_registry_errors),
        ("signal_scores", probe_mcp_signal_scores),
    ]

    for name, fn in probes:
        findings["probes"][name] = fn()

    # Derive summary flags
    conn = findings["probes"].get("connectivity", {})
    findings["summary"]["write_service_reachable"] = conn.get("reachable", False) and conn.get("response_valid", False)

    reg = findings["probes"].get("registry_integrity", {})
    findings["summary"]["registry_table_healthy"] = (
        reg.get("table_exists", False)
        and reg.get("has_required_columns", False)
        and reg.get("error") is None
    )

    sh = findings["probes"].get("service_health", {})
    findings["summary"]["daemons_reporting"] = (
        sh.get("table_exists", False)
        and len(sh.get("entries", [])) > 0
    )

    # Emit JSON
    output = json.dumps(findings, indent=2, default=str)
    print(output)


if __name__ == "__main__":
    run()
