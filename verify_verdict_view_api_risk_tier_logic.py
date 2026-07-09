# deps: pytest
"""
Verification module for verdict_view_api.py risk tier computation logic.

Smoke-tests _derive_overall_risk() and _risk_tier_from_overall() against
synthetic axis inputs mirroring the shape of mcp_llm_axis_scores rows.
"""

from __future__ import annotations
from typing import List


# --------------------------------------------------------------------------- #
# Copy of the pure helper functions from verdict_view_api.py.
# (verdict_view_api is async and pulls external services; we replicate only
#  the deterministic computation logic here so the self-test runs offline.)
# --------------------------------------------------------------------------- #
def _derive_overall_risk(axis_scores: List[float]) -> float:
    """Arithmetic mean of per-axis p_top values."""
    if not axis_scores:
        return 0.0
    return sum(axis_scores) / len(axis_scores)


def _risk_tier_from_overall(overall: float) -> str:
    """
    Map an overall risk float (0-1) to a tier string.

    PRODUCT_SPEC §2 thresholds:
        high   : overall >= 0.80
        medium : 0.50 <= overall < 0.80
        low    : overall < 0.50
    """
    if overall >= 0.80:
        return "high"
    if overall >= 0.50:
        return "medium"
    return "low"


def _apply_critical_override(tier: str, axis_scores: List[float],
                              p_critical_values: List[float]) -> str:
    """
    If any axis has p_critical > p_top, force tier to CRITICAL.
    Mirrors the rule-override logic described in PRODUCT_SPEC §2.
    """
    for p_top, p_crit in zip(axis_scores, p_critical_values):
        if p_crit is not None and p_top is not None and p_crit > p_top:
            return "critical"
    return tier


# --------------------------------------------------------------------------- #
# Synthetic test cases: 5 servers × all 7 axes.
# Axes: overall_risk, auth_strength, capability_breadth, data_sensitivity,
#       network_egress, maintainer_trust, exploit_surface
# p_top is normalised to [0, 1].
# --------------------------------------------------------------------------- #
# Each test case: (server_label, p_top values for 7 axes, p_critical values,
#                  expected tier before override, expected tier after override)
TEST_CASES = [
    # Case 1 – all low axes → expected "low"
    (
        "server_low_risk",
        [0.10, 0.15, 0.20, 0.25, 0.12, 0.18, 0.14],
        [None,  None,  None,  None,  None,  None,  None],
        "low",
        "low",
    ),
    # Case 2 – mixed low/medium → expected "medium"
    (
        "server_medium_risk",
        [0.55, 0.62, 0.48, 0.70, 0.51, 0.59, 0.53],
        [None,  None,  None,  None,  None,  None,  None],
        "medium",
        "medium",
    ),
    # Case 3 – all high axes → expected "high"
    (
        "server_high_risk",
        [0.85, 0.91, 0.88, 0.92, 0.81, 0.87, 0.90],
        [None,  None,  None,  None,  None,  None,  None],
        "high",
        "high",
    ),
    # Case 4 – borderline medium: mean is exactly 0.50 → expected "medium"
    (
        "server_borderline",
        [0.50, 0.50, 0.50, 0.50, 0.50, 0.50, 0.50],
        [None,  None,  None,  None,  None,  None,  None],
        "medium",
        "medium",
    ),
    # Case 5 – all axes medium/high BUT one axis with p_critical > p_top
    #          → CRITICAL override should fire
    (
        "server_critical_override",
        [0.60, 0.55, 0.65, 0.70, 0.58, 0.62, 0.59],
        [0.75,  None,  None,  None,  None,  None,  None],  # p_critical > p_top on overall_risk
        "medium",   # before override
        "critical", # after override
    ),
]


def run() -> None:
    all_passed = True

    for i, (label, p_tops, p_crits, expected_tier, expected_override) in enumerate(TEST_CASES, 1):
        try:
            # Derive overall risk (arithmetic mean)
            overall = _derive_overall_risk(p_tops)

            # Map to tier
            tier = _risk_tier_from_overall(overall)

            # Apply CRITICAL override
            final_tier = _apply_critical_override(tier, p_tops, p_crits)

            # Assertions
            assert tier == expected_tier, (
                f"[{label}] _risk_tier_from_overall({overall:.4f}) returned '{tier}', "
                f"expected '{expected_tier}'"
            )
            assert final_tier == expected_override, (
                f"[{label}] CRITICAL override returned '{final_tier}', "
                f"expected '{expected_override}'"
            )

            print(f"[PASS] {label}: overall={overall:.4f} tier={tier} final={final_tier}")

        except AssertionError as exc:
            print(f"[FAIL] {label}: {exc}")
            all_passed = False
        except Exception as exc:
            print(f"[FAIL] {label}: unexpected {type(exc).__name__}: {exc}")
            all_passed = False

    # Edge-case: empty axis list
    try:
        overall_empty = _derive_overall_risk([])
        assert overall_empty == 0.0, f"empty list should return 0.0, got {overall_empty}"
        tier_empty = _risk_tier_from_overall(0.0)
        assert tier_empty == "low", f"0.0 should map to 'low', got '{tier_empty}'"
        print("[PASS] edge-case: empty axis list → overall=0.0 tier=low")
    except AssertionError as exc:
        print(f"[FAIL] edge-case empty: {exc}")
        all_passed = False

    # Edge-case: exact threshold boundaries
    try:
        assert _risk_tier_from_overall(0.50) == "medium"
        assert _risk_tier_from_overall(0.80) == "high"
        assert _risk_tier_from_overall(0.49) == "low"
        assert _risk_tier_from_overall(0.79) == "medium"
        print("[PASS] edge-case: threshold boundaries")
    except AssertionError as exc:
        print(f"[FAIL] edge-case boundaries: {exc}")
        all_passed = False

    if all_passed:
        print("\nPASS")
    else:
        print("\nFAIL: one or more test cases failed")
        raise SystemExit(1)


if __name__ == "__main__":
    run()
