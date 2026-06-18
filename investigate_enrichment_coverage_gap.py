#!/usr/bin/env python3
"""
investigate_enrichment_coverage_gap.py

Investigates why mcp_signal_enrichments has only 12 rows for 1753 servers (0.7% coverage).

Checks:
  (1) enrichment_harness last execution timestamp
  (2) enrichment_pipeline_writer recent runs
  (3) count servers missing enrichment  (mcp_server_registry LEFT JOIN mcp_signal_enrichments)
  (4) which enrichment types are missing

Outputs diagnostic JSON with coverage_breakdown_by_enrichment_type.

DB access ONLY via write_service HTTP on 127.0.0.1:8772
"""

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
REQUEST_TIMEOUT = 30


def _http_request(method: str, url: str, data: dict = None, timeout: int = 30) -> dict:
    body = json.dumps(data).encode("utf-8") if data else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {body_err}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}")


def ws_query(sql: str, params: list = None) -> list:
    """Query via write_service /query endpoint."""
    payload = {"sql": sql, "params": params or []}
    result = _http_request("POST", f"{WRITE_SERVICE_URL}/query", payload, REQUEST_TIMEOUT)
    return result.get("rows", [])


def ws_execute(sql: str, params: list = None) -> dict:
    """Execute DML/DDL via write_service /execute endpoint."""
    payload = {"sql": sql, "params": params or []}
    return _http_request("POST", f"{WRITE_SERVICE_URL}/execute", payload, REQUEST_TIMEOUT)


# ---------------------------------------------------------------------------
# Check 1: enrichment_harness last execution timestamp
# ---------------------------------------------------------------------------
def get_harness_last_execution() -> dict:
    """
    Look for the most recent mcp_signal_enrichments row written by the harness.
    The harness populates server_id like '__harness_XX_XXXX__'.
    """
    result = {
        "found": False,
        "last_execution": None,
        "total_harness_rows": 0,
        "harness_servers": [],
        "error": None,
    }
    try:
        rows = ws_query(
            """
            SELECT
                MAX(computed_at) AS last_ts,
                COUNT(*)         AS cnt
            FROM mcp_signal_enrichments
            WHERE server_id LIKE '__harness_%'
            """
        )
        if rows:
            result["found"] = True
            result["last_execution"] = rows[0]["last_ts"]
            result["total_harness_rows"] = rows[0]["cnt"]
        # Also list distinct harness servers
        harness_servers = ws_query(
            "SELECT DISTINCT server_id FROM mcp_signal_enrichments WHERE server_id LIKE '__harness_%'"
        )
        result["harness_servers"] = [r["server_id"] for r in harness_servers]
    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Check 2: enrichment_pipeline_writer recent runs via service_health
# ---------------------------------------------------------------------------
def get_pipeline_writer_health() -> dict:
    """
    Query service_health for enrichment_pipeline_writer heartbeat state.
    """
    result = {
        "found": False,
        "last_heartbeat": None,
        "status": None,
        "meta": None,
        "error": None,
    }
    try:
        rows = ws_query(
            """
            SELECT service, status, meta, MAX(last_heartbeat) AS last_ts
            FROM service_health
            WHERE service LIKE '%enrichment%'
               OR service LIKE '%pipeline%'
               OR service LIKE '%harness%'
            GROUP BY service, status, meta
            ORDER BY last_ts DESC
            LIMIT 10
            """
        )
        if rows:
            result["found"] = True
            result["last_heartbeat"] = rows[0].get("last_ts")
            result["status"] = rows[0].get("status")
            result["meta"] = rows[0].get("meta")
            result["recent_entries"] = rows
    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Check 3: Count servers missing enrichment
# ---------------------------------------------------------------------------
def get_enrichment_gap() -> dict:
    """
    LEFT JOIN mcp_server_registry vs mcp_signal_enrichments.
    Returns servers that have no enrichment row at all.
    """
    result = {
        "total_registry_servers": 0,
        "servers_with_enrichment": 0,
        "servers_missing_enrichment": 0,
        "coverage_pct": 0.0,
        "missing_servers_sample": [],
        "error": None,
    }
    try:
        # Total servers in registry
        total_rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_server_registry")
        result["total_registry_servers"] = total_rows[0]["cnt"] if total_rows else 0

        # Servers that have at least one enrichment row
        enriched_rows = ws_query(
            """
            SELECT COUNT(DISTINCT e.server_id) AS cnt
            FROM mcp_signal_enrichments e
            """
        )
        result["servers_with_enrichment"] = enriched_rows[0]["cnt"] if enriched_rows else 0

        result["servers_missing_enrichment"] = (
            result["total_registry_servers"] - result["servers_with_enrichment"]
        )
        if result["total_registry_servers"] > 0:
            result["coverage_pct"] = round(
                100.0 * result["servers_with_enrichment"] / result["total_registry_servers"],
                2,
            )

        # Sample of missing servers
        missing_rows = ws_query(
            """
            SELECT r.server_id, r.name, r.registry_source, r.verdict, r.trust_score
            FROM mcp_server_registry r
            LEFT JOIN mcp_signal_enrichments e ON e.server_id = r.server_id
            WHERE e.server_id IS NULL
            LIMIT 20
            """
        )
        result["missing_servers_sample"] = missing_rows

    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Check 4: Breakdown by enrichment type (signal_type / dimension)
# ---------------------------------------------------------------------------
def get_coverage_by_enrichment_type() -> dict:
    """
    Count rows per signal_type and per dimension in mcp_signal_enrichments.
    """
    result = {
        "by_signal_type": [],
        "by_dimension": [],
        "distinct_signal_types": 0,
        "distinct_dimensions": 0,
        "error": None,
    }
    try:
        by_signal = ws_query(
            """
            SELECT
                signal_type,
                COUNT(*)        AS row_count,
                COUNT(DISTINCT server_id) AS server_count,
                MIN(computed_at) AS earliest,
                MAX(computed_at) AS latest
            FROM mcp_signal_enrichments
            GROUP BY signal_type
            ORDER BY row_count DESC
            """
        )
        result["by_signal_type"] = by_signal
        result["distinct_signal_types"] = len(by_signal)

        by_dim = ws_query(
            """
            SELECT
                dimension,
                COUNT(*)        AS row_count,
                COUNT(DISTINCT server_id) AS server_count,
                MIN(computed_at) AS earliest,
                MAX(computed_at) AS latest
            FROM mcp_signal_enrichments
            GROUP BY dimension
            ORDER BY row_count DESC
            """
        )
        result["by_dimension"] = by_dim
        result["distinct_dimensions"] = len(by_dim)

    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Check 5: Raw enrichment table stats
# ---------------------------------------------------------------------------
def get_enrichment_table_stats() -> dict:
    """
    Raw row counts and date range for mcp_signal_enrichments.
    """
    result = {
        "total_rows": 0,
        "earliest_computed_at": None,
        "latest_computed_at": None,
        "null_scores": 0,
        "error": None,
    }
    try:
        rows = ws_query(
            """
            SELECT
                COUNT(*)             AS total_rows,
                MIN(computed_at)     AS earliest,
                MAX(computed_at)     AS latest,
                SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS null_scores
            FROM mcp_signal_enrichments
            """
        )
        if rows:
            r = rows[0]
            result["total_rows"] = r.get("total_rows", 0) or 0
            result["earliest_computed_at"] = r.get("earliest")
            result["latest_computed_at"] = r.get("latest")
            result["null_scores"] = r.get("null_scores", 0) or 0
    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_diagnostic() -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    diagnostic = {
        "investigation": "enrichment_coverage_gap",
        "investigated_at": ts,
        "harness_execution": get_harness_last_execution(),
        "pipeline_writer_health": get_pipeline_writer_health(),
        "enrichment_gap": get_enrichment_gap(),
        "coverage_breakdown_by_enrichment_type": get_coverage_by_enrichment_type(),
        "enrichment_table_stats": get_enrichment_table_stats(),
    }
    return diagnostic


def main():
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] Starting enrichment coverage gap investigation...")
    try:
        diagnostic = run_diagnostic()
        print(json.dumps(diagnostic, indent=2, default=str))
        # Write to shared outputs
        out_path = "/home/workspace/zo_sentinel/shared/outputs/goose/enrichment_coverage_gap_diagnostic.json"
        import os
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(diagnostic, f, indent=2, default=str)
        print(f"[{datetime.now(timezone.utc).isoformat()}] Wrote diagnostic to {out_path}")
        return 0
    except Exception as e:
        print(f"[{datetime.now(timezone.utc).isoformat()}] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
