# github_repo_velocity_signal.py
"""
Pure enrichment module that evaluates a repository's “velocity” based on
metadata supplied by the caller.

The public API is a single function:

    compute_score(metadata: dict) -> (float, dict)

The function extracts four fields from ``metadata``:

* ``repo_name`` – name of the repository (string, required for evidence only)
* ``commit_count`` – total number of commits (int)
* ``contributor_count`` – number of distinct contributors (int)
* ``last_commit_date`` – ISO‑8601 date string (e.g. “2023-04-01”) of the most
  recent commit

A weighted linear combination of the three numeric signals produces a raw
score in the range ``0.0 … 1.0`` which is then scaled to ``0 … 100``.
Missing fields contribute ``0`` to the score and are recorded in the
evidence dictionary under the key ``'missing'``.

The evidence dictionary always contains:

* ``'verdict'`` – a textual classification derived from the final score.
* ``'missing'`` – a list of field names that were absent from the input.

No external services, databases or protected modules are used.
"""

from __future__ import annotations

import datetime
from typing import Tuple, Dict, List, Any

# --------------------------------------------------------------------------- #
# Configuration – weights must sum to 1.0
# --------------------------------------------------------------------------- #
WEIGHTS = {
    "commit_count": 0.4,
    "contributor_count": 0.3,
    "recency": 0.3,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

# Normalisation caps – chosen to map typical repository activity onto 0‑1.
MAX_COMMITS = 1000          # ≥1 000 commits → full score
MAX_CONTRIBUTORS = 100      # ≥100 contributors → full score
MAX_DAYS_SINCE = 365        # ≥365 days since last commit → zero score


def _normalise_commit_count(count: int) -> float:
    """Map commit count to 0‑1."""
    return min(max(count, 0) / MAX_COMMITS, 1.0)


def _normalise_contributor_count(count: int) -> float:
    """Map contributor count to 0‑1."""
    return min(max(count, 0) / MAX_CONTRIBUTORS, 1.0)


def _normalise_recency(last_commit_date: str) -> float:
    """
    Convert an ISO‑8601 date string to a recency score.
    More recent → higher score.
    """
    try:
        commit_dt = datetime.datetime.fromisoformat(last_commit_date)
    except Exception:
        # Invalid format – treat as missing
        return 0.0

    now = datetime.datetime.utcnow()
    days_since = (now - commit_dt).days
    if days_since < 0:
        # Future dates are treated as “just now”
        days_since = 0
    # Linear decay: 0 days → 1.0, MAX_DAYS_SINCE → 0.0
    return max(0.0, (MAX_DAYS_SINCE - days_since) / MAX_DAYS_SINCE)


def _classify_score(score: float) -> str:
    """Return a textual verdict based on the final 0‑100 score."""
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute a velocity score for a repository.

    Parameters
    ----------
    metadata: dict
        Expected keys are ``repo_name``, ``commit_count``, ``contributor_count``,
        and ``last_commit_date``.  Missing keys are tolerated.

    Returns
    -------
    (score, evidence) : tuple
        * ``score`` – float in the range 0 … 100.
        * ``evidence`` – dict with at least ``'verdict'`` and ``'missing'``.
    """
    missing: List[str] = []

    # repo_name is not used for scoring but we keep it for completeness.
    repo_name = metadata.get("repo_name")
    if repo_name is None:
        missing.append("repo_name")

    # ------------------------------------------------------------------- #
    # Commit count contribution
    # ------------------------------------------------------------------- #
    commit_raw = 0.0
    if "commit_count" in metadata:
        try:
            commit_raw = _normalise_commit_count(int(metadata["commit_count"]))
        except Exception:
            commit_raw = 0.0
            missing.append("commit_count")
    else:
        missing.append("commit_count")

    # ------------------------------------------------------------------- #
    # Contributor count contribution
    # ------------------------------------------------------------------- #
    contributor_raw = 0.0
    if "contributor_count" in metadata:
        try:
            contributor_raw = _normalise_contributor_count(
                int(metadata["contributor_count"])
            )
        except Exception:
            contributor_raw = 0.0
            missing.append("contributor_count")
    else:
        missing.append("contributor_count")

    # ------------------------------------------------------------------- #
    # Recency contribution
    # ------------------------------------------------------------------- #
    recency_raw = 0.0
    if "last_commit_date" in metadata:
        recency_raw = _normalise_recency(str(metadata["last_commit_date"]))
        if recency_raw == 0.0:
            # Could be an invalid date – treat as missing for evidence.
            missing.append("last_commit_date")
    else:
        missing.append("last_commit_date")

    # ------------------------------------------------------------------- #
    # Weighted aggregation (still 0‑1)
    # ------------------------------------------------------------------- #
    weighted_score = (
        WEIGHTS["commit_count"] * commit_raw
        + WEIGHTS["contributor_count"] * contributor_raw
        + WEIGHTS["recency"] * recency_raw
    )

    # Scale to 0‑100
    final_score = round(weighted_score * 100, 2)

    evidence: Dict[str, Any] = {
        "verdict": _classify_score(final_score),
        "missing": missing,
    }

    return final_score, evidence


# --------------------------------------------------------------------------- #
# Self‑test when executed as a script
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal metadata – only repo_name is supplied.
    test_meta = {"repo_name": "example"}
    score, ev = compute_score(test_meta)

    assert 0.0 <= score <= 100.0, "Score out of expected bounds"
    assert "verdict" in ev, "Evidence missing 'verdict' key"

    print(f"Self‑test PASS – score: {score}, verdict: {ev['verdict']}, missing: {ev['missing']}")