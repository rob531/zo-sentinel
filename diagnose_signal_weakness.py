#!/usr/bin/env python3
"""
diagnose_signal_weakness.py

This script investigates why the `known_bad_pattern` (distinct=2) and
`tool_count` (distinct=2) signals are reported as **WEAK – low variety**.

It queries the `mcp_signal_scores` data set for these two signal types
across *all* servers and produces a JSON diagnostic containing:

* the number of distinct score values,
* a histogram (value → occurrence count) of the scores,
* a mapping of scores that are shared by more than one MCP (i.e. MCPs
  that have identical scores).

The script **does not modify any enrichment files** – it only reads
the data and prints the diagnostic.
"""

import json
import os
import sys
from collections import defaultdict
from typing import Dict, List

import pandas as pd


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def _find_scores_file() -> str:
    """
    Locate the `mcp_signal_scores` CSV file.

    The repository may keep the file in a few conventional locations.
    If none are found, a ``FileNotFoundError`` is raised.
    """
    candidate_paths = [
        # Typical location next to this script
        os.path.join(os.path.dirname(__file__), "data", "mcp_signal_scores.csv"),
        # One level up (e.g. repo root /data)
        os.path.join(os.path.dirname(__file__), "..", "data", "mcp_signal_scores.csv"),
        # Directly beside the script (fallback)
        os.path.join(os.path.dirname(__file__), "mcp_signal_scores.csv"),
    ]

    for path in candidate_paths:
        if os.path.isfile(path):
            return os.path.abspath(path)

    raise FileNotFoundError(
        "Unable to locate `mcp_signal_scores.csv`. "
        "Searched paths: " + ", ".join(candidate_paths)
    )


def _load_scores_dataframe() -> pd.DataFrame:
    """
    Load the MCP signal scores into a pandas DataFrame.

    Expected columns (at minimum):
        - ``server``   : identifier of the server
        - ``mcp``      : identifier of the MCP (could be any unique key)
        - ``signal``   : name of the signal (e.g. ``known_bad_pattern``)
        - ``score``    : numeric score for that signal on that MCP
    """
    csv_path = _find_scores_file()
    df = pd.read_csv(csv_path)

    # Normalise column names to lower‑case for robustness
    df.columns = [c.strip().lower() for c in df.columns]

    required = {"server", "mcp", "signal", "score"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"The scores file is missing required columns: {', '.join(missing)}"
        )
    return df


def _analyze_signal(df: pd.DataFrame, signal_name: str) -> Dict:
    """
    Perform the analysis for a single signal type.

    Returns a dictionary with:
        - ``distinct_score_count`` (int)
        - ``score_histogram`` (dict of score → count)
        - ``identical_scores`` (dict of score → list of MCPs sharing that score)
    """
    # Filter to the requested signal
    signal_df = df[df["signal"] == signal_name]

    # If there is no data for this signal, return an empty report
    if signal_df.empty:
        return {
            "distinct_score_count": 0,
            "score_histogram": {},
            "identical_scores": {},
        }

    # Distinct score values
    distinct_score_count = int(signal_df["score"].nunique())

    # Histogram: score value → occurrence count
    hist_series = signal_df["score"].value_counts().sort_index()
    score_histogram = {float(k): int(v) for k, v in hist_series.items()}

    # Identify scores that are shared by more than one MCP
    identical_scores: Dict[float, List[str]] = {}
    for score_val, group in signal_df.groupby("score"):
        mcp_list = group["mcp"].astype(str).tolist()
        if len(mcp_list) > 1:
            identical_scores[float(score_val)] = mcp_list

    return {
        "distinct_score_count": distinct_score_count,
        "score_histogram": score_histogram,
        "identical_scores": identical_scores,
    }


def main() -> None:
    """
    Entry point: load data, analyse the two target signals and emit JSON.
    """
    try:
        df = _load_scores_dataframe()
    except Exception as exc:
        print(f"Error loading MCP signal scores: {exc}", file=sys.stderr)
        sys.exit(1)

    target_signals = ["known_bad_pattern", "tool_count"]
    diagnostic = {}

    for sig in target_signals:
        diagnostic[sig] = _analyze_signal(df, sig)

    # Pretty‑print the JSON diagnostic to stdout
    json.dump(diagnostic, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()