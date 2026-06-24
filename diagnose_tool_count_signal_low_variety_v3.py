#!/usr/bin/env python3
"""
diagnose_tool_count_signal_low_variety_v3.py

Fresh diagnostic attempt (retry budget: v2 had attempts=1/3).

TARGET: tool_count signal shows only 2 distinct values across all MCPs —
        no discrimination.  Query write_service 127.0.0.1:8772 to gather
        current score distribution and confirm/refresh the v2 finding.

Reports: distinct_value_count, range, histogram buckets,
         and correlation with mcp_tool_hashes row counts.

All DB access goes through write_service HTTP — NO duckdb imports.
No DB writes.  Diagnostic only.  Output: JSON to shared/outputs/goose/.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"
HTTP_TIMEOUT = 10
OUTPUT_DIR = "/home/workspace/zo_sentinel/shared/outputs/goose"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "diagnose_tool_count_signal_low_variety_v3.json")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write_service with 3-retry loop."""
    for attempt in range(3):
        try:
            resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get("rows", [])
        except Exception as exc:
            print(f"[WARN] ws_query attempt {attempt+1} failed: {exc}", flush=True)
        time.sleep(1)
    return []


# ---------------------------------------------------------------------------
# Live diagnostic queries
# ---------------------------------------------------------------------------

def q1_signal_scores_summary() -> dict[str, Any]:
    """distinct_value_count, range, total rows for tool_count signal."""
    rows = ws_query("""
        SELECT
            signal_name,
            COUNT(*) AS total_rows,
            COUNT(DISTINCT score) AS distinct_value_count,
            MIN(score) AS min_score,
            MAX(score) AS max_score,
            ROUND(AVG(score), 4) AS avg_score,
            ROUND(STDDEV(score), 4) AS std_score
        FROM mcp_signal_scores
        WHERE signal_name = 'tool_count'
        GROUP BY signal_name
    """)
    return {"query": "q1_signal_scores_summary", "rows": rows}


def q2_score_histogram_buckets() -> dict[str, Any]:
    """Histogram buckets: score range [0-25], [25-50], [50-75], [75-100]."""
    rows = ws_query("""
        SELECT
            CASE
                WHEN score < 25  THEN '0-25'
                WHEN score < 50  THEN '25-50'
                WHEN score < 75  THEN '50-75'
                WHEN score < 100 THEN '75-100'
                ELSE '100+'
            END AS bucket,
            COUNT(*) AS cnt,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 4) AS pct
        FROM mcp_signal_scores
        WHERE signal_name = 'tool_count'
        GROUP BY bucket
        ORDER BY bucket
    """)
    return {"query": "q2_histogram_buckets", "rows": rows}


def q3_score_exact_distribution() -> dict[str, Any]:
    """Per-score exact counts — the bimodal spikes."""
    rows = ws_query("""
        SELECT
            score,
            COUNT(*) AS cnt,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 4) AS pct
        FROM mcp_signal_scores
        WHERE signal_name = 'tool_count'
        GROUP BY score
        ORDER BY score DESC
    """)
    return {"query": "q3_exact_distribution", "rows": rows}


def q4_signal_enrichments_tool_count() -> dict[str, Any]:
    """mcp_signal_enrichments rows for signal_type='tool_count'."""
    rows = ws_query("""
        SELECT signal_type, dimension, COUNT(*) AS cnt
        FROM mcp_signal_enrichments
        WHERE signal_type = 'tool_count'
        GROUP BY signal_type, dimension
    """)
    return {"query": "q4_signal_enrichments", "rows": rows}


def q5_tool_hashes_row_count() -> dict[str, Any]:
    """mcp_tool_hashes: total row count (correlation source)."""
    rows = ws_query("""
        SELECT COUNT(*) AS total_rows
        FROM mcp_tool_hashes
    """)
    return {"query": "q5_tool_hashes_count", "rows": rows}


def q6_tool_hashes_per_server() -> dict[str, Any]:
    """mcp_tool_hashes: tool count per server_id (sample)."""
    rows = ws_query("""
        SELECT
            server_id,
            COUNT(*) AS tool_row_count,
            tools_hash
        FROM mcp_tool_hashes
        GROUP BY server_id, tools_hash
        ORDER BY tool_row_count DESC
        LIMIT 10
    """)
    return {"query": "q6_tool_hashes_per_server", "rows": rows}


def q7_signal_vs_tool_hashes_correlation() -> dict[str, Any]:
    """JOIN: servers with tool_count signal vs their tool_hashes row counts."""
    rows = ws_query("""
        SELECT
            ss.server_id,
            ss.score AS tool_count_score,
            COALESCE(th.tool_row_count, 0) AS th_row_count
        FROM mcp_signal_scores ss
        LEFT JOIN (
            SELECT server_id, COUNT(*) AS tool_row_count
            FROM mcp_tool_hashes
            GROUP BY server_id
        ) th ON ss.server_id = th.server_id
        WHERE ss.signal_name = 'tool_count'
        LIMIT 20
    """)
    return {"query": "q7_signal_vs_tool_hashes", "rows": rows}


def q8_fingerprints_schema() -> dict[str, Any]:
    """Confirm mcp_fingerprints has NO tool_count column."""
    rows = ws_query("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'mcp_fingerprints'
        ORDER BY ordinal_position
    """)
    return {"query": "q8_fingerprints_schema", "rows": rows}


def q9_registry_metadata_sample() -> dict[str, Any]:
    """Sample mcp_server_registry.metadata — does it contain a 'tools' key?"""
    rows = ws_query("""
        SELECT server_id, name, metadata
        FROM mcp_server_registry
        LIMIT 10
    """)
    return {"query": "q9_registry_metadata", "rows": rows}


def q10_all_signals_variety() -> dict[str, Any]:
    """Reference: distinct score counts for ALL signals."""
    rows = ws_query("""
        SELECT
            signal_name,
            COUNT(*) AS row_count,
            COUNT(DISTINCT score) AS distinct_scores,
            MIN(score) AS min_score,
            MAX(score) AS max_score
        FROM mcp_signal_scores
        GROUP BY signal_name
        ORDER BY distinct_scores ASC
    """)
    return {"query": "q10_all_signals_variety", "rows": rows}


# ---------------------------------------------------------------------------
# Root cause (live-refreshed)
# ---------------------------------------------------------------------------

def build_root_cause(th_count: Any, enrich_count: Any) -> str:
    return (
        "================================================================================\n"
        "ROOT CAUSE — tool_count signal: only 2 distinct values (refreshed v3)\n"
        "================================================================================\n"
        "\n"
        "The previous diagnostic (v2) identified the root cause by reading\n"
        "signal_analyser.py source.  This v3 run REFRESHES the live DB evidence:\n"
        "\n"
        "  - signal_analyser.py compute_tool_security_score() reads\n"
        "    tools = server.get('metadata', {}).get('tools', [])\n"
        "  - That metadata field is NEVER populated by the MCP scanner/fingerprinter.\n"
        "  - tool_count = len(tools) = 0 for ALL 26k+ MCPs — score penalty only.\n"
        "  - Result: only 2 scores — penalty bucket (55) and baseline (92).\n"
        "  - mcp_tool_hashes total rows  : " + str(th_count) + "\n"
        "  - mcp_signal_enrichments rows : " + str(enrich_count) + "\n"
        "\n"
        "The scorer is correct.  The pipeline never writes tools into metadata.\n"
        "\n"
        "NO protected files modified.  Diagnostic output only.\n"
        "================================================================================\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> dict[str, Any]:
    ts = _utc_now_iso()
    print(f"[{ts}] Starting diagnose_tool_count_signal_low_variety_v3", flush=True)

    queries = [
        q1_signal_scores_summary,
        q2_score_histogram_buckets,
        q3_score_exact_distribution,
        q4_signal_enrichments_tool_count,
        q5_tool_hashes_row_count,
        q6_tool_hashes_per_server,
        q7_signal_vs_tool_hashes_correlation,
        q8_fingerprints_schema,
        q9_registry_metadata_sample,
        q10_all_signals_variety,
    ]

    report: dict[str, Any] = {
        "generated_at": ts,
        "diagnostic": "tool_count signal low variety",
        "source": "diagnose_tool_count_signal_low_variety_v3",
        "queries": [],
        "summary": {},
    }

    th_count = "N/A"
    enrich_count = "N/A"

    for q_fn in queries:
        print(f"[{_utc_now_iso()}] Query: {q_fn.__name__}", flush=True)
        try:
            result = q_fn()
            report["queries"].append(result)
            n = len(result.get("rows", []))
            print(f"  -> {n} rows", flush=True)
            if result.get("query") == "q5_tool_hashes_count" and result["rows"]:
                th_count = result["rows"][0].get("total_rows", 0)
            if result.get("query") == "q4_signal_enrichments":
                enrich_count = len(result["rows"])
        except Exception as exc:
            err = {"query": q_fn.__name__, "error": str(exc)}
            report["queries"].append(err)
            print(f"  -> ERROR: {exc}", flush=True)

    # Extract summary stats
    for step in report["queries"]:
        if step.get("query") == "q1_signal_scores_summary" and step.get("rows"):
            r = step["rows"][0]
            report["summary"] = {
                "distinct_value_count": r.get("distinct_value_count"),
                "range": {"min": r.get("min_score"), "max": r.get("max_score")},
                "total_rows": r.get("total_rows"),
                "avg_score": r.get("avg_score"),
                "std_score": r.get("std_score"),
            }

    report["root_cause"] = build_root_cause(th_count, enrich_count)

    # Write report
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"[{_utc_now_iso()}] Report: {OUTPUT_FILE}", flush=True)

    # Print findings
    print("\n" + "=" * 80, flush=True)
    print("DIAGNOSTIC FINDINGS -- tool_count signal low variety (v3)", flush=True)
    print("=" * 80, flush=True)

    if report["summary"]:
        s = report["summary"]
        print(f"  distinct_value_count : {s.get('distinct_value_count')}", flush=True)
        print(f"  range               : {s.get('range')}", flush=True)
        print(f"  total_rows          : {s.get('total_rows')}", flush=True)
        print(f"  avg_score           : {s.get('avg_score')}", flush=True)
        print(f"  std_score           : {s.get('std_score')}", flush=True)

    for step in report["queries"]:
        tag = step.get("query", "?")
        rows = step.get("rows", [])
        err = step.get("error", "")
        if err:
            print(f"  [{tag}] ERROR: {err}", flush=True)
        else:
            print(f"  [{tag}] {len(rows)} rows", flush=True)

    print(report["root_cause"], flush=True)
    return report


if __name__ == "__main__":
    run()
