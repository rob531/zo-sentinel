#!/usr/bin/env python3
"""
verify_known_bad_pattern_enrichment_v2_effectiveness.py

Diagnostic script that validates the effectiveness of the
`known_bad_pattern_enrichment_v2` signal.  It queries the
`mcp_signal_scores` data source for all servers that have a
`signal_type` of ``known_bad_pattern`` and checks two simple
invariants:

* **Distinct score count** – the number of unique scores must be at
  least 20.
* **Score range spread** – the difference between the maximum and
  minimum score must be at least 20.0.

If either condition is not met the script exits with a non‑zero
status code and prints a short diagnostic message; otherwise it
exits with status ``0`` indicating the signal provides sufficient
discrimination across servers.

The script is deliberately lightweight and does not depend on any
external configuration – it imports the ``mcp_signal_scores`` module
(which is expected to expose a ``query`` function returning an
iterable of rows with ``server_id`` and ``score`` fields).  If the
module cannot be imported or the query fails, the script also exits
with a non‑zero status and prints an error message.
"""

import sys
import traceback
from typing import Iterable, Tuple

# ----------------------------------------------------------------------
# Helper – abstract the data‑access layer.
# ----------------------------------------------------------------------
def _fetch_known_bad_pattern_scores() -> Tuple[float, ...]:
    """
    Retrieve all scores for the ``known_bad_pattern`` signal.

    Returns
    -------
    tuple of float
        A tuple containing the score for each server that reported the
        ``known_bad_pattern`` signal.

    Raises
    ------
    ImportError
        If the ``mcp_signal_scores`` module cannot be imported.
    Exception
        If the query fails for any other reason.
    """
    try:
        # The repository is expected to provide a module named
        # ``mcp_signal_scores`` with a ``query`` function that accepts a
        # ``signal_type`` keyword argument and returns an iterable of
        # rows.  Each row must expose a ``score`` attribute (or key).
        import mcp_signal_scores
    except Exception as exc:
        raise ImportError(
            "Unable to import the required 'mcp_signal_scores' module."
        ) from exc

    try:
        # The concrete implementation of ``query`` may return a list,
        # generator, pandas DataFrame, etc.  We only need the ``score``
        # field, so we normalise the result to a simple tuple of floats.
        raw_rows: Iterable = mcp_signal_scores.query(signal_type="known_bad_pattern")
    except Exception as exc:
        raise Exception(
            "Failed to query 'mcp_signal_scores' for known_bad_pattern."
        ) from exc

    scores = []
    for row in raw_rows:
        # Support both dict‑like and attribute‑style access.
        if isinstance(row, dict):
            score = row.get("score")
        else:
            score = getattr(row, "score", None)

        if score is None:
            # Skip rows without a score – this should not happen in a
            # well‑behaved data source but we guard against it.
            continue

        try:
            scores.append(float(score))
        except (TypeError, ValueError):
            # If the score cannot be coerced to a float we ignore it.
            continue

    return tuple(scores)


# ----------------------------------------------------------------------
# Core diagnostic logic.
# ----------------------------------------------------------------------
def _evaluate_effectiveness(scores: Tuple[float, ...]) -> Tuple[bool, str]:
    """
    Evaluate the signal effectiveness based on the supplied scores.

    Parameters
    ----------
    scores: tuple of float
        All scores for the ``known_bad_pattern`` signal.

    Returns
    -------
    (bool, str)
        * ``True``  – the signal meets both effectiveness criteria.
        * ``False`` – one or both criteria are not satisfied.
        * The accompanying string contains a human‑readable diagnostic.
    """
    if not scores:
        return (
            False,
            "No scores were found for signal_type='known_bad_pattern'. "
            "Cannot assess effectiveness.",
        )

    distinct_count = len(set(scores))
    score_range = max(scores) - min(scores)

    if distinct_count < 20 and score_range < 20.0:
        return (
            False,
            f"Signal is weak: only {distinct_count} distinct scores "
            f"and a range of {score_range:.2f} (both below thresholds).",
        )
    if distinct_count < 20:
        return (
            False,
            f"Signal is weak: only {distinct_count} distinct scores "
            f"(threshold is 20).",
        )
    if score_range < 20.0:
        return (
            False,
            f"Signal is weak: score range is {score_range:.2f} "
            f"(threshold is 20.0).",
        )

    return (
        True,
        f"Signal passes effectiveness checks: {distinct_count} distinct "
        f"scores, range {score_range:.2f}.",
    )


# ----------------------------------------------------------------------
# Entry point.
# ----------------------------------------------------------------------
def main() -> int:
    """
    Run the diagnostic and exit with an appropriate status code.

    Returns
    -------
    int
        ``0`` if the signal meets the effectiveness criteria,
        ``1`` otherwise.
    """
    try:
        scores = _fetch_known_bad_pattern_scores()
    except Exception as exc:
        print("[ERROR] Failed to obtain scores:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    passed, message = _evaluate_effectiveness(scores)

    if passed:
        print("[OK]   Known‑bad‑pattern enrichment v2 effectiveness verified.")
        print("       ", message)
        return 0
    else:
        print("[FAIL] Known‑bad‑pattern enrichment v2 effectiveness check failed.", file=sys.stderr)
        print("       ", message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())