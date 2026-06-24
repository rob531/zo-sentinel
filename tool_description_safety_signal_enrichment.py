# deps: (stdlib only)

"""
tool_description_safety_signal_enrichment.py
--------------------------------------------
Pure enrichment module that evaluates tool-description metadata and returns
a safety score (0-100) together with an evidence dictionary.

Field used:
  - tool_description : free-text description of the tool (e.g. "A safe tool")

Missing fields contribute 0 to the weighted score and are recorded in
evidence['missing'].

The weighted contribution of each field sums to 1.0, yielding a final
score in the range [0, 100].
"""

from __future__ import annotations

__all__ = ["compute_score"]

import re

# ---------------------------------------------------------------------------
# Weight definitions (must sum to 1.0)
# ---------------------------------------------------------------------------
_WEIGHTS = {
    "tool_description": 1.0,
}
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------
# Compiled once at import time: whole-word match for each safety-positive
# keyword. Word boundaries prevent false positives like "unsafe" matching
# "safe", "insecure" matching "secure", etc.
_SAFE_KEYWORDS = ("safe", "secure", "protected", "guarded", "trusted", "verified")
_SAFE_PATTERNS = tuple(re.compile(rf"\b{re.escape(kw)}\b") for kw in _SAFE_KEYWORDS)


def _norm_tool_description(value):
    """Return 1.0 if tool description contains a safety-positive keyword as
    a whole word, otherwise 0.0. None / empty -> 0.0."""
    if value is None:
        return 0.0
    try:
        text = str(value).strip().lower()
    except Exception:
        return 0.0
    if not text:
        return 0.0
    return 1.0 if any(p.search(text) for p in _SAFE_PATTERNS) else 0.0


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------
def compute_score(metadata: dict):
    """
    Evaluate ``metadata`` and return a tuple:

    (score, evidence)

    *score*    -- float in the inclusive range [0, 100].

    *evidence* -- dict with the following keys:
        - ``verdict``: one of ``"low"``, ``"medium"``, ``"high"`` based on
          score thresholds (0-30 low, 31-70 medium, 71-100 high).
        - ``missing``: list of field names that were absent from ``metadata``.

    Signal Invariant (PRODUCT_SPEC §3):
        compute_score(metadata: dict) -> (float in [0,100], evidence dict)
        Pure function: no DB writes, no network.
    """
    if not isinstance(metadata, dict):
        # Defensive: treat non-dict input as empty
        metadata = {}

    missing_fields = []

    def get_field(key):
        if key not in metadata:
            missing_fields.append(key)
            return None
        return metadata[key]

    # Extract and normalise tool_description
    norm_description = _norm_tool_description(get_field("tool_description"))

    # Compute weighted sum (weights sum to 1.0)
    weighted_sum = norm_description * _WEIGHTS["tool_description"]

    # Scale to 0-100 and clamp for safety against float drift
    score = max(0.0, min(100.0, weighted_sum * 100.0))

    # Determine verdict
    if score <= 30.0:
        verdict = "low"
    elif score <= 70.0:
        verdict = "medium"
    else:
        verdict = "high"

    evidence = {
        "verdict": verdict,
        "missing": missing_fields,
    }

    return score, evidence


# ---------------------------------------------------------------------------
# Self-test (run with ``python tool_description_safety_signal_enrichment.py``)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Test 1: Required acceptance case from the directive.
    score, evidence = compute_score({"tool_description": "A safe tool"})
    assert 0.0 <= score <= 100.0, f"Score out of range: {score}"
    assert isinstance(evidence, dict), "Evidence is not a dict"
    assert "verdict" in evidence, "Evidence missing 'verdict' key"
    assert "missing" in evidence, "Evidence missing 'missing' key"
    # 'A safe tool' contains the 'safe' keyword -> score == 100.0, verdict 'high'
    assert score == 100.0, f"Expected 100.0 for safe description, got {score}"
    assert evidence["verdict"] == "high", f"Expected verdict 'high', got {evidence['verdict']}"
    assert len(evidence["missing"]) == 0, "No fields should be missing here"

    # Test 2: Unsafe description -> low score, verdict 'low'.
    score2, evidence2 = compute_score({"tool_description": "An unsafe tool"})
    assert 0.0 <= score2 <= 100.0, f"Score out of range: {score2}"
    assert evidence2["verdict"] in ("low", "medium", "high"), "Bad verdict"
    assert score2 == 0.0, f"Expected 0.0 for unsafe description, got {score2}"
    assert evidence2["verdict"] == "low", f"Expected verdict 'low', got {evidence2['verdict']}"

    # Test 3: Empty metadata -> all fields missing, score 0.
    score3, evidence3 = compute_score({})
    assert 0.0 <= score3 <= 100.0, f"Empty score out of range: {score3}"
    assert evidence3["missing"] == ["tool_description"], \
        f"Expected ['tool_description'] missing, got {evidence3['missing']}"
    assert score3 == 0.0, f"Expected 0.0 for empty metadata, got {score3}"

    print("PASS")
