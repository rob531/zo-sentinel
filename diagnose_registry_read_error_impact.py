#!/usr/bin/env python3
# deps: requests
"""
Diagnostic utility to investigate the 'Registry read error' reported in
current build state.

Investigates:
  1. write_service connectivity at :8772
  2. mcp_server_registry table integrity via information_schema.columns
  3. Recent daemon heartbeats from service_health table
  4. audit_log for registry-read-error events
  5. mcp_signal_scores FK alignment with registry

Diagnostic-only module: no DB writes, no daemon registration.
Output: structured JSON to stdout for operator review.
"""
import json
import time
from datetime import datetime, timezone
from typing import Any

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_TIMEOUT = 10
DIAGNOSTIC_NAME = "diagnose_registry_read_error_impact"


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


# ------------------------------------------------------------------
# Probe 1: write_service connectivity
# ------------------------------------------------------------------
def probe_write_service_connectivity() -> dict[str, Any]:
    """Ping write_service /query endpoint to verify it is responding."""
    started = time.monotonic()
    try:
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
    columns with types. Also check row count and sample rows.
    """
    result: dict[str, Any] = {
        "probe": "mcp_server_registry_integrity",
        "table_exists": False,
        "columns": [],
        "row_count": None,
        "has_required_columns": False,
        "missing_required_columns": [],
        "sample_rows": [],
        "null_server_id_count": None,
        "error": None,
    }

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
        result["missing_required_columns"] = sorted(required - present)

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

        # Sample rows
        try:
            result["sample_rows"] = ws_query(
                "SELECT server_id, name, url, enabled, verdict, trust_score "
                "FROM mcp_server_registry LIMIT 5"
            )
        except Exception as e:
            result["sample_error"] = str(e)

        # Null server_id check (data integrity)
        try:
            null_rows = ws_query(
                "SELECT COUNT(*) AS cnt FROM mcp_server_registry WHERE server_id IS NULL"
            )
            result["null_server_id_count"] = null_rows[0]["cnt"] if null_rows else 0
        except Exception as e:
            result["null_check_error"] = str(e)

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
# Probe 5: mcp_signal_scores FK alignment with registry
# ------------------------------------------------------------------
def probe_mcp_signal_scores_alignment() -> dict[str, Any]:
    """
    Check mcp_signal_scores exists, count rows, and verify FK alignment
    by finding orphaned signal_scores rows (server_id not in registry).
    """
    result: dict[str, Any] = {
        "probe": "mcp_signal_scores_alignment",
        "table_exists": False,
        "row_count": None,
        "sample": [],
        "orphaned_count": None,
        "error": None,
    }

    try:
        col_rows = ws_query("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'mcp_signal_scores'
        """)
        result["table_exists"] = len(col_rows) > 0

        if result["table_exists"]:
            count_rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_signal_scores")
            result["row_count"] = count_rows[0]["cnt"] if count_rows else 0

            result["sample"] = ws_query(
                "SELECT server_id, signal_name, score, evidence, scored_at "
                "FROM mcp_signal_scores ORDER BY scored_at DESC LIMIT 3"
            )

            # Orphaned rows: signal_scores.server_id not in registry
            try:
                orphan_rows = ws_query("""
                    SELECT COUNT(*) AS cnt
                    FROM mcp_signal_scores s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM mcp_server_registry r
                        WHERE r.server_id = s.server_id
                    )
                """)
                result["orphaned_count"] = orphan_rows[0]["cnt"] if orphan_rows else 0
            except Exception as e:
                result["orphan_check_error"] = str(e)

    except Exception as e:
        result["error"] = str(e)

    return result


# ------------------------------------------------------------------
# Probe 6: Read success rate on mcp_server_registry (are ANY reads working?)
# ------------------------------------------------------------------
def probe_registry_read_success_rate() -> dict[str, Any]:
    """
    Attempt multiple simple reads against mcp_server_registry to determine
    whether reads are partially/fully succeeding or fully failing.
    """
    result: dict[str, Any] = {
        "probe": "registry_read_success_rate",
        "attempts": 0,
        "successes": 0,
        "failures": 0,
        "failure_messages": [],
        "read_succeeded": False,
    }

    test_queries = [
        ("count_all", "SELECT COUNT(*) AS cnt FROM mcp_server_registry"),
        ("select_limit", "SELECT server_id, name FROM mcp_server_registry LIMIT 1"),
        ("select_where", "SELECT server_id FROM mcp_server_registry WHERE enabled = TRUE LIMIT 1"),
        ("information_schema", "SELECT table_name FROM information_schema.tables WHERE table_name = 'mcp_server_registry'"),
    ]

    for name, sql in test_queries:
        result["attempts"] += 1
        try:
            rows = ws_query(sql)
            result["successes"] += 1
            if result["attempts"] == 1:
                result["count_value"] = rows[0]["cnt"] if rows and "cnt" in rows[0] else None
        except Exception as e:
            result["failures"] += 1
            result["failure_messages"].append(f"{name}: {str(e)}")

    result["read_succeeded"] = result["successes"] > 0
    return result


# ------------------------------------------------------------------
# Probe 7: Check for malformed data in sample rows
# ------------------------------------------------------------------
def probe_malformed_data() -> dict[str, Any]:
    """
    Scan for nulls, empty strings, and unexpected types in key columns.
    """
    result: dict[str, Any] = {
        "probe": "malformed_data_check",
        "null_server_id": None,
        "empty_name_count": None,
        "empty_description_count": None,
        "non_bool_enabled_count": None,
        "error": None,
    }

    checks = [
        ("null_server_id", "SELECT COUNT(*) AS cnt FROM mcp_server_registry WHERE server_id IS NULL"),
        ("empty_name_count", "SELECT COUNT(*) AS cnt FROM mcp_server_registry WHERE name IS NULL OR name = ''"),
        ("empty_description_count", "SELECT COUNT(*) AS cnt FROM mcp_server_registry WHERE description IS NULL OR description = ''"),
        ("non_bool_enabled_count", "SELECT COUNT(*) AS cnt FROM mcp_server_registry WHERE enabled NOT IN (TRUE, FALSE)"),
    ]

    for key, sql in checks:
        try:
            rows = ws_query(sql)
            result[key] = rows[0]["cnt"] if rows else 0
        except Exception as e:
            result[f"{key}_error"] = str(e)

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
            "registry_reads_succeeding": False,
            "daemons_reporting": False,
            "data_quality_ok": False,
        },
    }

    # Run probes
    probes = [
        ("connectivity", probe_write_service_connectivity),
        ("registry_integrity", probe_mcp_server_registry_integrity),
        ("service_health", probe_service_health_recent),
        ("audit_log", probe_audit_log_registry_errors),
        ("signal_scores_alignment", probe_mcp_signal_scores_alignment),
        ("read_success_rate", probe_registry_read_success_rate),
        ("malformed_data", probe_malformed_data),
    ]

    for name, fn in probes:
        findings["probes"][name] = fn()

    # Derive summary flags
    conn = findings["probes"].get("connectivity", {})
    findings["summary"]["write_service_reachable"] = (
        conn.get("reachable", False) and conn.get("response_valid", False)
    )

    reg = findings["probes"].get("registry_integrity", {})
    findings["summary"]["registry_table_healthy"] = (
        reg.get("table_exists", False)
        and reg.get("has_required_columns", False)
        and reg.get("error") is None
    )

    rdr = findings["probes"].get("read_success_rate", {})
    findings["summary"]["registry_reads_succeeding"] = rdr.get("read_succeeded", False)

    sh = findings["probes"].get("service_health", {})
    findings["summary"]["daemons_reporting"] = (
        sh.get("table_exists", False)
        and len(sh.get("entries", [])) > 0
    )

    mfd = findings["probes"].get("malformed_data", {})
    findings["summary"]["data_quality_ok"] = (
        (mfd.get("null_server_id") or 0) == 0
        and (mfd.get("non_bool_enabled_count") or 0) == 0
    )

    # Emit JSON
    output = json.dumps(findings, indent=2, default=str)
    print(output)


if __name__ == "__main__":
    run()