#!/usr/bin/env python3
"""
Diagnostic utility to verify that the most recent enrichment modules are
properly wired into the `mcp_signal_enrichments` table.

The script:
1. Connects to the MCP database (via a SQLAlchemy URL supplied in the
   environment variable ``MCP_DB_URL`` or via the ``--db-url`` CLI flag).
2. Queries the ``mcp_signal_enrichments`` table for a row count per enrichment.
3. Checks the set of *recent* enrichments that should be present:
   - known_bad_pattern_enrichment_v2
   - tool_count_enrichment_v2
   - tool_description_safety_enrichment
   - temporal_stability_enrichment_v2
   - permission_scope_enrichment_v2
4. Prints a short report showing the row count for each enrichment.
5. Emits ``PASS`` if every recent enrichment has at least one row,
   otherwise emits ``FAIL`` together with the list of missing enrichments.

The script is deliberately lightweight – it only needs a read‑only connection
to the database and does not depend on any internal MCP code.
"""

import os
import sys
import argparse
from collections import defaultdict

try:
    from sqlalchemy import create_engine, text
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: SQLAlchemy is required to run this diagnostic utility.\n"
        "Install it with `pip install sqlalchemy`.\n"
    )
    raise exc


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# The list of enrichment modules that were added recently and must be wired.
RECENT_ENRICHMENTS = [
    "known_bad_pattern_enrichment_v2",
    "tool_count_enrichment_v2",
    "tool_description_safety_enrichment",
    "temporal_stability_enrichment_v2",
    "permission_scope_enrichment_v2",
]

# Name of the table that stores enrichment results.
ENRICHMENT_TABLE = "mcp_signal_enrichments"

# Column that stores the enrichment identifier.  The MCP schema historically
# uses the name ``enrichment``; if a different column is used, adjust the
# constant below.
ENRICHMENT_COLUMN = "enrichment"


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def get_engine(db_url: str):
    """Create a SQLAlchemy engine from a URL."""
    return create_engine(db_url)


def fetch_counts(engine):
    """
    Return a mapping ``{enrichment_name: row_count}`` for every enrichment
    present in the ``mcp_signal_enrichments`` table.
    """
    query = text(
        f"""
        SELECT
            {ENRICHMENT_COLUMN} AS enrichment,
            COUNT(*)          AS cnt
        FROM {ENRICHMENT_TABLE}
        GROUP BY {ENRICHMENT_COLUMN}
        """
    )
    with engine.connect() as conn:
        result = conn.execute(query)
        counts = {row["enrichment"]: row["cnt"] for row in result}
    return counts


def report(counts):
    """
    Print a human‑readable report and return ``True`` if all recent enrichments
    have at least one row, otherwise ``False``.
    """
    missing = []
    print("\n=== Enrichment wiring verification ===\n")
    for enr in RECENT_ENRICHMENTS:
        cnt = counts.get(enr, 0)
        status = "OK" if cnt > 0 else "MISSING"
        print(f"{enr:40s} : {cnt:6d} rows  -> {status}")
        if cnt == 0:
            missing.append(enr)

    print("\n--- Summary ---")
    if not missing:
        print("PASS: All recent enrichments have evidence rows.")
        return True
    else:
        print("FAIL: The following enrichments have no rows (not wired):")
        for enr in missing:
            print(f"  - {enr}")
        return False


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def parse_args():
    parser = argparse.ArgumentParser(
        description="Verify that recent enrichment modules are wired into "
                    "`mcp_signal_enrichments`."
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("MCP_DB_URL"),
        help=(
            "SQLAlchemy database URL. If omitted, the environment variable "
            "`MCP_DB_URL` is consulted."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.db_url:
        sys.stderr.write(
            "ERROR: No database URL supplied. Provide it via --db-url or the "
            "MCP_DB_URL environment variable.\n"
        )
        sys.exit(2)

    try:
        engine = get_engine(args.db_url)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"ERROR: Could not create DB engine: {exc}\n")
        sys.exit(2)

    try:
        counts = fetch_counts(engine)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"ERROR: Query failed: {exc}\n")
        sys.exit(2)

    success = report(counts)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()