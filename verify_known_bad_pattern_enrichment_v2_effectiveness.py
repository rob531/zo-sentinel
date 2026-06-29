#!/usr/bin/env python3
"""
verify_known_bad_pattern_enrichment_v2_effectiveness.py

Diagnostic script that verifies the effectiveness of the
`known_bad_pattern_enrichment_v2.py` signal implementation.

The script queries the `mcp_signal_scores` table for the
`known_bad_pattern` signal across **all** servers, then computes:

* The number of distinct score values observed.
* The numeric spread of the scores (max – min).

According to the specification (section 3 – signal invariant), a
signal is only useful if it discriminates between servers.  The
diagnostic fails (non‑zero exit status) when either:

* fewer than 20 distinct scores are observed, **or**
* the score spread is less than 20.0.

If both conditions are satisfied the script exits with status 0 and
reports a PASS; otherwise it exits with status 1 and reports a FAIL.

The script is deliberately self‑contained – it uses only the standard
library and expects an SQLite database file named ``mcp.db`` in the
current working directory (or the path supplied via the
``MCP_DB_PATH`` environment variable).
"""

import os
import sys
import sqlite3
from pathlib import Path

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DEFAULT_DB_PATH = Path("mcp.db")
DB_PATH = Path(os.getenv("MCP_DB_PATH", DEFAULT_DB_PATH))

SIGNAL_TYPE = "known_bad_pattern"
MIN_DISTINCT_SCORES = 20
MIN_SCORE_SPREAD = 20.0


def fetch_scores(conn: sqlite3.Connection):
    """
    Retrieve all scores for the target signal type.

    Returns
    -------
    list of float
        The raw score values (one per server entry).
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT score
        FROM mcp_signal_scores
        WHERE signal_type = ?
        """,
        (SIGNAL_TYPE,),
    )
    rows = cur.fetchall()
    return [row[0] for row in rows]


def evaluate(scores):
    """
    Compute the distinct count and spread for a list of scores.

    Parameters
    ----------
    scores : list of float

    Returns
    -------
    tuple (int, float)
        (distinct_score_count, score_spread)
    """
    if not scores:
        return 0, 0.0

    distinct = set(scores)
    distinct_count = len(distinct)
    score_spread = max(distinct) - min(distinct)
    return distinct_count, score_spread


def main():
    if not DB_PATH.is_file():
        print(f"ERROR: Database file not found at '{DB_PATH}'.", file=sys.stderr)
        sys.exit(2)

    try:
        conn = sqlite3.connect(str(DB_PATH))
    except sqlite3.Error as exc:
        print(f"ERROR: Unable to open database: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        scores = fetch_scores(conn)
    finally:
        conn.close()

    distinct_count, score_spread = evaluate(scores)

    print(f"Signal type:            {SIGNAL_TYPE}")
    print(f"Total records examined: {len(scores)}")
    print(f"Distinct scores:        {distinct_count}")
    print(f"Score spread:           {score_spread:.2f}")

    # Determine pass/fail according to the spec
    if distinct_count < MIN_DISTINCT_SCORES or score_spread < MIN_SCORE_SPREAD:
        print(
            f"FAIL: Signal discrimination insufficient ("
            f"distinct < {MIN_DISTINCT_SCORES} or spread < {MIN_SCORE_SPREAD})."
        )
        sys.exit(1)

    print("PASS: Signal discrimination meets the required thresholds.")
    sys.exit(0)


if __name__ == "__main__":
    main()