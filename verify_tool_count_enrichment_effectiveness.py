#!/usr/bin/env python3
"""
verify_tool_count_enrichment_effectiveness.py

This script validates that the *tool_count* enrichment module is being
executed by the signal analysis pipeline and that it produces a
sufficiently diverse set of scores across the server population.

The verification steps are:

1. Query the ``mcp_signal_scores`` table for the ``tool_count`` dimension.
2. Compute:
   * The number of distinct scores.
   * The minimum and maximum score values.
   * A simple histogram (5‑bin distribution) to visualise the spread.
3. Inspect the ``signal_analyser`` pipeline configuration to confirm that
   ``tool_count_enrichment`` is part of the execution chain.
4. Emit a concise report and exit with a non‑zero status if the score
   variety is below an acceptable threshold (default: 5 distinct scores).

The script is deliberately self‑contained – it only relies on the public
interfaces provided by the repository (``mcp_signal_scores`` and
``signal_analyser``).  No external configuration is required.
"""

from __future__ import annotations

import sys
import argparse
from collections import Counter
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Repository imports – these are expected to exist in the zo‑sentinel code‑base.
# ---------------------------------------------------------------------------
try:
    # ``mcp_signal_scores`` provides a simple query interface.
    from mcp_signal_scores import fetch_scores_by_dimension
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: Could not import `fetch_scores_by_dimension` from "
        "`mcp_signal_scores`. Ensure the module is present and importable.\n"
    )
    raise exc

try:
    # ``signal_analyser`` holds the pipeline definition.
    from signal_analyser import PIPELINE_STEPS
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "ERROR: Could not import `PIPELINE_STEPS` from `signal_analyser`. "
        "Make sure the analyser module is available.\n"
    )
    raise exc


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def distinct_score_stats(scores: List[float]) -> Tuple[int, float, float]:
    """Return (distinct_count, min_score, max_score) for a list of scores."""
    if not scores:
        raise ValueError("Score list is empty – cannot compute statistics.")
    distinct = len(set(scores))
    return distinct, min(scores), max(scores)


def histogram(scores: List[float], bins: int = 5) -> List[Tuple[float, float, int]]:
    """
    Build a simple histogram.

    Returns a list of tuples ``(bin_start, bin_end, count)``.
    The bins are equally spaced between the global min and max (inclusive).
    """
    if bins < 1:
        raise ValueError("Number of bins must be >= 1")
    if not scores:
        return []

    lo, hi = min(scores), max(scores)
    # Guard against zero width (all scores identical)
    if lo == hi:
        return [(lo, hi, len(scores))]

    bin_width = (hi - lo) / bins
    # Create bin edges: [lo, lo+width, ..., hi]
    edges = [lo + i * bin_width for i in range(bins + 1)]
    # Count occurrences per bin
    counts = [0] * bins
    for s in scores:
        # Clamp the index to the last bin for the max value
        idx = min(int((s - lo) / bin_width), bins - 1)
        counts[idx] += 1

    return [
        (edges[i], edges[i + 1], counts[i]) for i in range(bins)
    ]


def check_pipeline_for_enrichment(step_name: str = "tool_count_enrichment") -> bool:
    """
    Verify that the enrichment step appears in the analyser pipeline.

    ``PIPELINE_STEPS`` is expected to be an iterable of step identifiers
    (strings or callables).  The function returns ``True`` if the supplied
    ``step_name`` is present.
    """
    # Normalise to string representation for comparison
    step_names = {
        getattr(s, "__name__", str(s)).lower() for s in PIPELINE_STEPS
    }
    return step_name.lower() in step_names


# ---------------------------------------------------------------------------
# Main verification routine
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that the tool_count enrichment produces a diverse set "
            "of scores and that it is invoked by the signal analyser."
        )
    )
    parser.add_argument(
        "--min-distinct",
        type=int,
        default=5,
        help="Minimum number of distinct scores required for a healthy variety.",
    )
    parser.add_argument(
        "--bins",
        type=int,
        default=5,
        help="Number of histogram bins to display.",
    )
    args = parser.parse_args()

    # -------------------------------------------------------------------
    # 1. Pull scores for the `tool_count` dimension.
    # -------------------------------------------------------------------
    try:
        scores = fetch_scores_by_dimension("tool_count")
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"ERROR: Failed to fetch scores: {exc}\n")
        return 1

    if not scores:
        sys.stderr.write(
            "ERROR: No scores returned for dimension 'tool_count'. "
            "Check that the enrichment module is populating the table.\n"
        )
        return 1

    # -------------------------------------------------------------------
    # 2. Compute distinct count and range.
    # -------------------------------------------------------------------
    try:
        distinct_cnt, min_score, max_score = distinct_score_stats(scores)
    except ValueError as exc:  # pragma: no cover
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    # -------------------------------------------------------------------
    # 3. Build a histogram for quick visual inspection.
    # -------------------------------------------------------------------
    hist = histogram(scores, bins=args.bins)

    # -------------------------------------------------------------------
    # 4. Verify pipeline configuration.
    # -------------------------------------------------------------------
    enrichment_present = check_pipeline_for_enrichment()

    # -------------------------------------------------------------------
    # 5. Emit report.
    # -------------------------------------------------------------------
    print("\n=== Tool‑Count Enrichment Effectiveness Report ===\n")
    print(f"Total scores examined          : {len(scores)}")
    print(f"Distinct score values          : {distinct_cnt}")
    print(f"Score range                    : {min_score:.2f} – {max_score:.2f}")
    print("\nScore distribution (histogram):")
    for bin_start, bin_end, count in hist:
        bar = "#" * min(count, 50)  # simple visual bar, capped at 50 chars
        print(f"  [{bin_start:8.2f} – {bin_end:8.2f}] : {count:3d} {bar}")

    print("\nPipeline inspection:")
    print(f"  tool_count_enrichment present : {'YES' if enrichment_present else 'NO'}")

    # -------------------------------------------------------------------
    # 6. Determine exit status based on thresholds.
    # -------------------------------------------------------------------
    if not enrichment_present:
        sys.stderr.write(
            "\nERROR: The enrichment step is NOT part of the signal analyser "
            "pipeline. The scores observed may not be the result of the "
            "intended enrichment.\n"
        )
        return 2

    if distinct_cnt < args.min_distinct:
        sys.stderr.write(
            f"\nWARNING: Only {distinct_cnt} distinct scores were produced. "
            f"The configured minimum is {args.min_distinct}. This indicates "
            "low score variety (e.g., a WEAK diagnostic).\n"
        )
        return 3

    print("\nResult: SUCCESS – sufficient score variety detected.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())