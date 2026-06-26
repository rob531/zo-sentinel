# deps: 
"""trust_gating_override.py

Implements logic for overriding risk tiers based on LLM axis scores.

Public interface:
    apply_trust_gating_override(current_risk_tier: str, llm_axis_scores: dict) -> str

The function is pure: no DB writes, no network calls.

The rule set is simplified for this implementation:

- The system defines risk tiers: "LOW", "MEDIUM", "HIGH", "CRITICAL".
- Each LLM axis score entry is a dict with keys:
    - "score": a numeric value (0-100) representing the confidence for that axis.
    - "p_top": a numeric probability (0-1) indicating the top‑prediction probability.

Override rules (illustrative, can be extended):

1. If any axis has a score < 20, the risk tier is forced to "CRITICAL".
2. If any axis has a score between 20 and 40 (inclusive) and the current tier is not "CRITICAL",
   the tier is upgraded to at least "HIGH".
3. If any axis has a score between 41 and 60 (inclusive) and the current tier is "LOW",
   the tier is upgraded to "MEDIUM".
4. Otherwise, the original tier is retained.

These rules emulate the "rule‑override (a CRITICAL axis forces the tier)" described in the
`mcp_verdict_detail_api.py` documentation.

The function returns the final risk tier as a string.
"""

from typing import Dict, Any

# Define the ordered risk tiers for comparison
_RISK_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

def _tier_index(tier: str) -> int:
    """Return the index of a tier in the ordered list, defaulting to 0 for unknown tiers."""
    try:
        return _RISK_ORDER.index(tier.upper())
    except ValueError:
        return 0

def apply_trust_gating_override(current_risk_tier: str, llm_axis_scores: Dict[str, Dict[str, Any]]) -> str:
    """Potentially override the risk tier based on LLM axis scores.

    Parameters
    ----------
    current_risk_tier: str
        The existing risk tier (e.g., "LOW", "MEDIUM", "HIGH", "CRITICAL").
    llm_axis_scores: dict
        Mapping from axis name to a dict containing at least a numeric ``score``.
        Example::
            {
                "reliability": {"score": 15, "p_top": 0.8},
                "accuracy": {"score": 55, "p_top": 0.6},
            }

    Returns
    -------
    str
        The possibly overridden risk tier.
    """
    # Normalise the current tier
    current_tier = current_risk_tier.upper()
    # Guard against unknown tiers – treat them as LOW
    if current_tier not in _RISK_ORDER:
        current_tier = "LOW"

    # Early exit if any axis forces CRITICAL
    for axis, details in llm_axis_scores.items():
        score = details.get("score")
        if isinstance(score, (int, float)) and score < 20:
            return "CRITICAL"

    # Determine the highest required tier based on the remaining rules
    required_tier = current_tier
    for axis, details in llm_axis_scores.items():
        score = details.get("score")
        if not isinstance(score, (int, float)):
            continue
        if 20 <= score <= 40:
            # Upgrade to at least HIGH (unless already CRITICAL)
            if _tier_index(required_tier) < _tier_index("HIGH"):
                required_tier = "HIGH"
        elif 41 <= score <= 60:
            # Upgrade to at least MEDIUM if currently LOW
            if required_tier == "LOW":
                required_tier = "MEDIUM"
        # Scores above 60 do not cause an upgrade.

    return required_tier

# ---------------------------------------------------------------------------
# Self‑test harness
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Define test cases as tuples: (current_tier, llm_axis_scores, expected_tier)
    test_cases = [
        # Critical override due to low score
        ("LOW", {"axis1": {"score": 10, "p_top": 0.9}}, "CRITICAL"),
        # Upgrade to HIGH because a score is in 20‑40 range
        ("MEDIUM", {"axis1": {"score": 30, "p_top": 0.7}}, "HIGH"),
        # Upgrade to MEDIUM from LOW because a score is in 41‑60 range
        ("LOW", {"axis1": {"score": 50, "p_top": 0.5}}, "MEDIUM"),
        # No change when scores are high
        ("HIGH", {"axis1": {"score": 85, "p_top": 0.95}}, "HIGH"),
        # Multiple axes, lowest rule wins (CRITICAL)
        ("MEDIUM", {"a": {"score": 35}, "b": {"score": 15}}, "CRITICAL"),
        # Multiple axes, highest upgrade applies (HIGH)
        ("LOW", {"a": {"score": 35}, "b": {"score": 55}}, "HIGH"),
        # Unknown current tier defaults to LOW then upgrades
        ("UNKNOWN", {"axis": {"score": 45}}, "MEDIUM"),
    ]

    all_passed = True
    for idx, (cur, scores, expected) in enumerate(test_cases, 1):
        result = apply_trust_gating_override(cur, scores)
        if result != expected:
            all_passed = False
            print(f"Test {idx} FAILED: cur={cur}, scores={scores} => got {result}, expected {expected}")
        else:
            print(f"Test {idx} passed.")

    if all_passed:
        print("ALL TESTS PASSED")
    else:
        raise SystemExit("Some tests failed")
