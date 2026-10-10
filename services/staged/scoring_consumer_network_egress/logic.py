from typing import Tuple, Dict, Any

# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def compute_tier(row: Dict[str, Any]) -> Tuple[float, str, Dict[str, Any]]:
    """
    Compute a risk score (0‑100), a risk tier string and an evidence dict
    from a raw ``mcp_llm_axis_scores`` row.

    Parameters
    ----------
    row: dict
        A mapping representing a row from ``mcp_llm_axis_scores``.  Expected
        keys are ``p_top``, ``p_critical``, ``label`` (and optionally others).

    Returns
    -------
    tuple[float, str, dict]
        * **score** – ``p_top`` scaled to ``0‑100`` (clamped).
        * **tier** – one of the six tier strings:
          ``critical``, ``high``, ``moderate``, ``low``, ``info``, ``none``.
        * **evidence** – a dict containing the raw values that contributed
          to the calculation.
    """
    # Extract raw values, defaulting to ``None`` when missing.
    p_top = row.get("p_top")
    p_critical = row.get("p_critical")
    label = row.get("label")

    # ------------------------------------------------------------------
    # Score calculation
    # ------------------------------------------------------------------
    # ``p_top`` is a probability in the range ``0‑1``.  The service defines
    # the risk score as that probability multiplied by 100.
    try:
        raw_score = float(p_top) * 100.0 if p_top is not None else 0.0
    except (TypeError, ValueError):
        raw_score = 0.0

    # Clamp to the inclusive range ``0‑100``.
    score = max(0.0, min(100.0, raw_score))

    # ------------------------------------------------------------------
    # Tier determination (six‑tier model)
    # ------------------------------------------------------------------
    if score >= 90.0:
        tier = "critical"
    elif score >= 75.0:
        tier = "high"
    elif score >= 50.0:
        tier = "moderate"
    elif score >= 25.0:
        tier = "low"
    elif score > 0.0:
        tier = "info"
    else:
        tier = "none"

    # ------------------------------------------------------------------
    # Evidence payload
    # ------------------------------------------------------------------
    evidence = {
        "p_top": p_top,
        "p_critical": p_critical,
        "label": label,
    }

    return score, tier, evidence


# ----------------------------------------------------------------------
# Minimal self‑test (executed when the module is run directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    test_rows = [
        {"p_top": 0.0, "p_critical": 0.1, "label": "A"},
        {"p_top": 0.2, "p_critical": 0.2, "label": "B"},
        {"p_top": 0.4, "p_critical": 0.3, "label": "C"},
        {"p_top": 0.6, "p_critical": 0.4, "label": "D"},
        {"p_top": 0.8, "p_critical": 0.5, "label": "E"},
        {"p_top": 1.0, "p_critical": 0.6, "label": "F"},
    ]

    expected_tiers = ["none", "info", "low", "moderate", "high", "critical"]
    for row, exp_tier in zip(test_rows, expected_tiers):
        score, tier, ev = compute_tier(row)
        assert 0.0 <= score <= 100.0, "Score out of bounds"
        assert tier == exp_tier, f"Tier mismatch: {tier} != {exp_tier}"
        assert isinstance(ev, dict), "Evidence not a dict"
    print("PASS")