# deps: 
"""
tool_count_signal_enhancer.py

Enrichment module for the tool_count signal.
Implements fine‑grained scoring buckets for tool_count and applies a penalty
based on schema complexity (total_schema_fields). The function is pure – no
DB writes, no network calls – and conforms to the enrichment contract:

    compute_score(metadata: dict) -> (float, dict)

where the float is in [0, 100] and the dict provides evidence of the
calculation.
"""

from __future__ import annotations
from typing import Any, Dict, Tuple

# ---------------------------------------------------------------------------
# Bucket definitions for tool_count (inclusive bounds)
# Each tuple is (low_inclusive, high_inclusive, low_score, high_score)
# Scores are linearly interpolated within the bucket.
# ---------------------------------------------------------------------------
_TOOL_COUNT_BUCKETS = [
    (1, 5, 85, 100),
    (6, 15, 70, 84),
    (16, 30, 50, 69),
    (31, 50, 30, 49),
    (51, None, 10, 29),  # None means no upper bound
]


def _interpolate(value: int, low: int, high: int, low_score: float, high_score: float) -> float:
    """Linearly map *value* from [low, high] to [low_score, high_score].
    If *high* is None (open‑ended bucket) the *low_score* is returned.
    """
    if high is None:
        # Open‑ended bucket – use the low_score as a baseline and add a small
        # diminishing return based on how far the value is beyond the low bound.
        # The exact formula is not critical; we keep it simple.
        excess = value - low
        # Diminish by 0.1 per extra tool, capped at the bucket's high_score.
        return max(low_score, min(high_score, low_score + excess * 0.1))
    if high == low:
        return low_score
    ratio = (value - low) / (high - low)
    return low_score + ratio * (high_score - low_score)


def _score_from_tool_count(tool_count: int) -> float:
    """Return a base score derived from *tool_count* using the defined buckets."""
    for low, high, low_score, high_score in _TOOL_COUNT_BUCKETS:
        if high is None:
            if tool_count >= low:
                return _interpolate(tool_count, low, high, low_score, high_score)
        elif low <= tool_count <= high:
            return _interpolate(tool_count, low, high, low_score, high_score)
    # If tool_count is zero or negative, treat as the lowest possible score.
    return 0.0


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Compute a fine‑grained score for the *tool_count* signal.

    Expected keys in *metadata* (all optional):
        - tool_count (int or float)
        - total_schema_fields (int) – total number of fields across all tool
          schemas; used as a penalty.
        - avg_tool_name_length (int or float) – average length of tool names.
        - has_dynamic_tools (bool) – whether the package contains dynamically
          generated tools.

    Returns:
        (score, evidence) where *score* is a float in the range 0‑100 and
        *evidence* is a dictionary describing the intermediate values.
    """
    # -------------------------------------------------------------------
    # Gather raw inputs with safe defaults.
    # -------------------------------------------------------------------
    raw: Dict[str, Any] = {}
    raw["tool_count"] = int(metadata.get("tool_count", 0))
    raw["total_schema_fields"] = int(metadata.get("total_schema_fields", 0))
    raw["avg_tool_name_length"] = float(metadata.get("avg_tool_name_length", 0.0))
    raw["has_dynamic_tools"] = bool(metadata.get("has_dynamic_tools", False))

    # -------------------------------------------------------------------
    # Base score from tool_count buckets.
    # -------------------------------------------------------------------
    base_score = _score_from_tool_count(raw["tool_count"])

    # -------------------------------------------------------------------
    # Penalties based on schema complexity.
    #   * total_schema_fields: 0.1 point penalty per field, capped at 20.
    #   * avg_tool_name_length > 20: subtract 5 points.
    #   * has_dynamic_tools: subtract 10 points.
    # -------------------------------------------------------------------
    penalty = 0.0
    penalty += min(20.0, raw["total_schema_fields"] * 0.1)
    if raw["avg_tool_name_length"] > 20:
        penalty += 5.0
    if raw["has_dynamic_tools"]:
        penalty += 10.0

    # Apply penalty, ensuring the final score stays within [0, 100].
    final_score = max(0.0, min(100.0, base_score - penalty))
    final_score = round(final_score, 2)

    # -------------------------------------------------------------------
    # Build evidence dictionary.
    # -------------------------------------------------------------------
    evidence: Dict[str, Any] = {
        "raw_inputs": raw,
        "base_score": round(base_score, 2),
        "penalty": round(penalty, 2),
        "final_score": final_score,
        "bucket_used": None,
    }
    # Identify which bucket was used for transparency.
    for low, high, low_score, high_score in _TOOL_COUNT_BUCKETS:
        if high is None:
            if raw["tool_count"] >= low:
                evidence["bucket_used"] = f"{low}+"
                break
        elif low <= raw["tool_count"] <= high:
            evidence["bucket_used"] = f"{low}-{high}"
            break

    return final_score, evidence


if __name__ == "__main__":
    # Self‑smoke test – three representative inputs.
    test_cases = [
        {
            "label": "small package, simple schema",
            "metadata": {
                "tool_count": 3,
                "total_schema_fields": 5,
                "avg_tool_name_length": 12,
                "has_dynamic_tools": False,
            },
        },
        {
            "label": "medium package, complex schema",
            "metadata": {
                "tool_count": 20,
                "total_schema_fields": 120,
                "avg_tool_name_length": 25,
                "has_dynamic_tools": True,
            },
        },
        {
            "label": "large package, many tools",
            "metadata": {
                "tool_count": 60,
                "total_schema_fields": 300,
                "avg_tool_name_length": 18,
                "has_dynamic_tools": False,
            },
        },
    ]

    for tc in test_cases:
        score, ev = compute_score(tc["metadata"])
        assert 0.0 <= score <= 100.0, f"Score out of range for {tc['label']}"
        print(f"{tc['label']:<30} -> score: {score:5.2f}, bucket: {ev.get('bucket_used')}")
    print("OK: tool_count_signal_enhancer self‑smoke passed.")
