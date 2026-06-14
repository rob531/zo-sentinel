#!/usr/bin/env python3
"""
verify_enrichment_pipeline.py

End-to-end diagnostic script to verify the enrichment pipeline is functional
and mcp_signal_enrichments is being populated.

PURPOSE: Confirm that enrichments_writer.py, supply_chain_enrichment.py, and the
write_service connectivity all work together end-to-end.

INTERFACE: python3 verify_enrichment_pipeline.py
EXIT CODE: 0 on PASS (mcp_signal_enrichments >= 1 row), 1 on FAIL (empty)

CONSTRAINTS:
  - Read-only: uses write_service POST /query only, no /write or /execute
  - No DB imports (duckdb, sqlite3, etc.)
  - No file writes
  - No network beyond write_service on 127.0.0.1:8772
  - Stdlib + requests only

deps: requests
"""

import sys
import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE}/query"


def ws_query(sql: str) -> dict:
    """Execute a SELECT query against write_service. Returns parsed JSON."""
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        return {"error": str(exc), "rows": [], "count": 0}


def count_rows(table: str) -> int:
    """Return total row count for a table, or -1 on error."""
    result = ws_query(f"SELECT COUNT(*) AS cnt FROM {table}")
    if "error" in result or not result.get("rows"):
        return -1
    return result["rows"][0].get("cnt", result["rows"][0].get("COUNT(*)", 0))


def group_by_signal_type(table: str) -> dict:
    """
    SELECT signal_type, COUNT(*) AS cnt FROM <table>
    GROUP BY signal_type ORDER BY cnt DESC.
    Returns {signal_type: count, ...} or empty dict on error.
    """
    result = ws_query(
        f"SELECT signal_type, COUNT(*) AS cnt "
        f"FROM {table} "
        f"GROUP BY signal_type "
        f"ORDER BY cnt DESC"
    )
    if "error" in result:
        return {}
    out = {}
    for row in result.get("rows", []):
        st = row.get("signal_type", "(null)")
        cnt = row.get("cnt", row.get("count", 0))
        out[st] = cnt
    return out


def total_mcp_count() -> int:
    """Return total registered MCP count from mcp_server_registry."""
    return count_rows("mcp_server_registry")


def enriched_mcp_count() -> int:
    """
    Count distinct server_ids that have at least one enrichment row.
    Uses COUNT(DISTINCT server_id) so we compare apples-to-apples with
    the total registered count.
    """
    result = ws_query(
        "SELECT COUNT(DISTINCT server_id) AS cnt "
        "FROM mcp_signal_enrichments"
    )
    if "error" in result or not result.get("rows"):
        return 0
    return result["rows"][0].get("cnt", result["rows"][0].get("COUNT(DISTINCT server_id)", 0))


def sample_enrichment_rows(limit: int = 5) -> list:
    """Fetch a few sample rows to show what the data looks like."""
    result = ws_query(
        "SELECT signal_type, score, computed_at "
        "FROM mcp_signal_enrichments "
        "LIMIT " + str(limit)
    )
    if "error" in result:
        return []
    return result.get("rows", [])


def print_table(header: list, rows: list) -> None:
    """Print a simple left-aligned text table."""
    if not rows:
        print("  (empty)")
        return
    # Compute column widths
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    # Separator
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    # Header
    print(sep)
    print("|" + "|".join(f" {h:<{widths[i]}} " for i, h in enumerate(header)) + "|")
    print(sep)
    # Body
    for row in rows:
        print("|" + "|".join(f" {str(row[i]):<{widths[i]}} " for i in range(len(row))) + "|")
    print(sep)


def main() -> int:
    print("=" * 70)
    print("ENRICHMENT PIPELINE VERIFICATION")
    print("=" * 70)
    print()

    # ── 1. mcp_signal_enrichments grouped by signal_type ──────────────────
    print("[1] mcp_signal_enrichments — rows by signal_type")
    print("-" * 70)
    enrich_counts = group_by_signal_type("mcp_signal_enrichments")
    total_enrich_rows = sum(enrich_counts.values())
    if enrich_counts:
        header = ["signal_type", "row_count"]
        rows = [[st, cnt] for st, cnt in enrich_counts.items()]
        print_table(header, rows)
        print(f"  Total rows in mcp_signal_enrichments: {total_enrich_rows}")
    else:
        print("  (no rows found)")
    print()

    # ── 2. mcp_signal_scores grouped by signal_type ───────────────────────
    print("[2] mcp_signal_scores — rows by signal_type (for comparison)")
    print("-" * 70)
    scores_counts = group_by_signal_type("mcp_signal_scores")
    total_scores_rows = sum(scores_counts.values())
    if scores_counts:
        header = ["signal_type", "row_count"]
        rows = [[st, cnt] for st, cnt in scores_counts.items()]
        print_table(header, rows)
        print(f"  Total rows in mcp_signal_scores: {total_scores_rows}")
    else:
        print("  (no rows found)")
    print()

    # ── 3. Ratio of enriched MCPs to total registered MCPs ─────────────────
    print("[3] Enrichment coverage")
    print("-" * 70)
    total_registered = total_mcp_count()
    distinct_enriched = enriched_mcp_count()
    if total_registered > 0:
        ratio = distinct_enriched / total_registered
        pct = ratio * 100
        print(f"  Registered MCPs (mcp_server_registry): {total_registered}")
        print(f"  Enriched MCPs  (distinct server_ids in enrichments): {distinct_enriched}")
        print(f"  Coverage ratio: {distinct_enriched}/{total_registered} = {pct:.1f}%")
    else:
        print(f"  Registered MCPs: {total_registered}  (registry may be empty)")
        print(f"  Enriched MCPs: {distinct_enriched}")
    print()

    # ── 4. Sample rows ─────────────────────────────────────────────────────
    if total_enrich_rows > 0:
        print("[4] Sample rows from mcp_signal_enrichments")
        print("-" * 70)
        samples = sample_enrichment_rows(limit=5)
        if samples:
            header = ["signal_type", "score", "computed_at"]
            rows = [
                [
                    str(r.get("signal_type", "")),
                    str(r.get("score", "")),
                    str(r.get("computed_at", "")),
                ]
                for r in samples
            ]
            print_table(header, rows)
        print()

    # ── 5. PASS / FAIL verdict ─────────────────────────────────────────────
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)

    if total_enrich_rows >= 1:
        print()
        print("  PASS")
        print(f"  mcp_signal_enrichments contains {total_enrich_rows} row(s) across "
              f"{len(enrich_counts)} signal_type(s).")
        print("  The enrichment pipeline is functional and data is flowing.")
        print()
        print("=" * 70)
        return 0
    else:
        print()
        print("  FAIL")
        print("  mcp_signal_enrichments is empty — no enrichment rows found.")
        print("  The pipeline may not be wired, or the writer daemon is not running.")
        print("  Check: enrichments_writer.py, supply_chain_enrichment.py, and")
        print("         write_service connectivity at 127.0.0.1:8772.")
        print()
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())