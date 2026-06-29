#!/usr/bin/env python3
"""
Verification utility to check enrichment pipeline coverage across all 8 signals.

The script performs three read‑only queries (via the `write_service` helper):
1. Counts of enriched rows per `signal_type` from ``mcp_signal_enrichments``.
2. Total number of registered servers from ``mcp_server_registry``.
3. Distribution of rows per `signal_type` from ``mcp_signal_scores``.

It then calculates coverage percentages, flags signals whose enrichment
coverage falls below a configurable threshold (default 80 %), and emits a
JSON report that includes:

* total number of servers,
* per‑signal enrichment coverage,
* per‑signal score‑row distribution,
* a list of recommendations for signals that need more wiring work.

No data is written back to the database – the script is pure analysis.
"""

import json
import logging
import sys
from typing import Dict, List, Any

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
# Minimum acceptable enrichment coverage (percentage). Signals below this
# value will be listed in the recommendations section.
MIN_COVERAGE_PERCENT = 80.0

# --------------------------------------------------------------------------- #
# Helper – thin wrapper around the write_service read‑only API
# --------------------------------------------------------------------------- #
try:
    # The repository provides a ``write_service`` module that offers a
    # ``read_sql`` helper returning a list of dictionaries (one per row).
    from write_service import write_service as ws
except Exception as exc:  # pragma: no cover
    sys.stderr.write(
        "Unable to import `write_service`. This script must be run inside the "
        "zo‑sentinel environment where the service is available.\n"
    )
    raise exc


def _run_query(query: str) -> List[Dict[str, Any]]:
    """
    Execute a read‑only SQL query via ``write_service`` and return the rows.

    Parameters
    ----------
    query: str
        The SQL statement to execute.

    Returns
    -------
    List[Dict[str, Any]]
        List of rows where each row is represented as a ``dict`` mapping column
        names to values.
    """
    try:
        rows = ws.read_sql(query)          # type: ignore[attr-defined]
        if not isinstance(rows, list):
            raise TypeError("write_service.read_sql did not return a list")
        return rows
    except Exception as exc:               # pragma: no cover
        logging.error("SQL query failed: %s", exc)
        raise


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def gather_counts() -> Dict[str, Any]:
    """
    Pull the three required data sets from the database.

    Returns
    -------
    dict
        {
            "total_servers": int,
            "enrichment_counts": {signal_type: int, ...},
            "score_counts": {signal_type: int, ...}
        }
    """
    # 1️⃣ Total servers
    total_srv_q = "SELECT COUNT(*) AS total FROM mcp_server_registry"
    total_srv_row = _run_query(total_srv_q)[0]
    total_servers = int(total_srv_row["total"])

    # 2️⃣ Enrichment rows per signal
    enrich_q = """
        SELECT signal_type, COUNT(*) AS cnt
        FROM mcp_signal_enrichments
        GROUP BY signal_type
    """
    enrich_rows = _run_query(enrich_q)
    enrichment_counts = {r["signal_type"]: int(r["cnt"]) for r in enrich_rows}

    # 3️⃣ Score rows per signal
    scores_q = """
        SELECT signal_type, COUNT(*) AS cnt
        FROM mcp_signal_scores
        GROUP BY signal_type
    """
    score_rows = _run_query(scores_q)
    score_counts = {r["signal_type"]: int(r["cnt"]) for r in score_rows}

    return {
        "total_servers": total_servers,
        "enrichment_counts": enrichment_counts,
        "score_counts": score_counts,
    }


def build_report(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert raw counts into a structured JSON‑serialisable report.

    Parameters
    ----------
    data: dict
        Output of :func:`gather_counts`.

    Returns
    -------
    dict
        The final report.
    """
    total_servers = data["total_servers"]
    enrichment_counts = data["enrichment_counts"]
    score_counts = data["score_counts"]

    # ------------------------------------------------------------------- #
    # Enrichment coverage per signal
    # ------------------------------------------------------------------- #
    enrichment_coverage = {}
    low_coverage_signals = []

    for signal, enriched in enrichment_counts.items():
        coverage_pct = (enriched / total_servers) * 100 if total_servers else 0.0
        enrichment_coverage[signal] = {
            "enriched_servers": enriched,
            "coverage_percent": round(coverage_pct, 2),
        }
        if coverage_pct < MIN_COVERAGE_PERCENT:
            low_coverage_signals.append(signal)

    # ------------------------------------------------------------------- #
    # Score distribution per signal (relative to total score rows)
    # ------------------------------------------------------------------- #
    total_score_rows = sum(score_counts.values())
    score_distribution = {}
    for signal, cnt in score_counts.items():
        pct = (cnt / total_score_rows) * 100 if total_score_rows else 0.0
        score_distribution[signal] = {
            "score_rows": cnt,
            "percent_of_total_scores": round(pct, 2),
        }

    # ------------------------------------------------------------------- #
    # Recommendations
    # ------------------------------------------------------------------- #
    recommendations = []
    if low_coverage_signals:
        for sig in low_coverage_signals:
            recommendations.append(
                f"Signal '{sig}' has enrichment coverage "
                f"{enrichment_coverage[sig]['coverage_percent']}% "
                f"(< {MIN_COVERAGE_PERCENT}%). Consider wiring additional "
                "servers to its enrichment module."
            )
    else:
        recommendations.append(
            "All signals meet the minimum coverage threshold."
        )

    report = {
        "total_servers": total_servers,
        "enrichment_coverage": enrichment_coverage,
        "score_distribution": score_distribution,
        "recommendations": recommendations,
    }
    return report


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.info("Gathering enrichment pipeline statistics…")
    data = gather_counts()
    report = build_report(data)

    # Pretty‑print JSON to stdout – this is the script’s sole output.
    json.dump(report, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")  # ensure trailing newline


if __name__ == "__main__":  # pragma: no cover
    main()