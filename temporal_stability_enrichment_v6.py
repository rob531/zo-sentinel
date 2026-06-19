# deps: requests
"""
temporal_stability_enrichment_v6.py
Pure enrichment module for temporal_stability signal.
Signature: compute_score(metadata: dict) -> tuple[float, dict]
Reads: age_days, community_signal, supply_chain, download_count
"""
import math
import logging
from datetime import datetime, timezone
from typing import Any

LOG = logging.getLogger(__name__)

SIGNAL_NAME = "temporal_stability"
VERSION = "v6"
MAX_SCORE = 100.0


def _sigmoid(x: float) -> float:
    """Clamped sigmoid for stable numeric output."""
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _clamp(value: float, lo: float = 0.0, hi: float = MAX_SCORE) -> float:
    return max(lo, min(hi, value))


# ------------------------------------------------------------------
# Component scorers
# ------------------------------------------------------------------

def _score_age_bracket(age_days: Any) -> float:
    """
    Age bracket bonus per spec:
      0-30d  -> 0
      30-180d -> 15
      180-365d -> 25
      365d+  -> 35
    """
    if age_days is None:
        return 0.0
    try:
        age = float(age_days)
    except (TypeError, ValueError):
        return 0.0

    if age < 0:
        return 0.0
    elif age < 30:
        return 0.0
    elif age < 180:
        return 15.0
    elif age < 365:
        return 25.0
    else:
        return 35.0


def _score_consistency_modifier(
    community_signal: Any,
    download_count: Any,
) -> tuple[float, str]:
    """
    Consistency modifier based on community_signal volatility proxy
    (download_count trend).  We use download_count as a proxy for
    whether community_signal is trending up/down/stable.

    community_signal numeric -> use it directly as volatility indicator.
    community_signal string   -> map categories to volatility scores.
    download_count           -> normalize to [0,1] as a confidence weight.

    Returns (modifier_points, detail_str).
    Modifier range: -10 to +10.
    """
    if community_signal is None and download_count is None:
        return 0.0, "no_data"

    # --- download_count normalization (confidence/weight proxy) ---
    download_score = 50.0  # default mid-point
    if download_count is not None:
        try:
            dc = float(download_count)
            if dc <= 0:
                download_score = 10.0
            elif dc < 100:
                download_score = 20.0
            elif dc < 1000:
                download_score = 40.0
            elif dc < 10000:
                download_score = 60.0
            elif dc < 100000:
                download_score = 80.0
            else:
                download_score = 95.0
        except (TypeError, ValueError):
            pass

    # --- community_signal as volatility indicator ---
    # High signal + high downloads = consistent/stable -> positive modifier
    # Low signal + low downloads  = uncertain/noisy   -> neutral
    # We treat community_signal as a normalised 0-100 score.
    cs_value: float | None = None
    if community_signal is not None:
        if isinstance(community_signal, (int, float)):
            cs_value = float(community_signal)
        else:
            cs_str = str(community_signal).lower().strip()
            cs_map = {
                "high": 80.0,
                "very high": 95.0,
                "active": 75.0,
                "moderate": 55.0,
                "medium": 55.0,
                "low": 30.0,
                "very low": 15.0,
                "none": 5.0,
                "inactive": 5.0,
                "unknown": 40.0,
            }
            for key, val in cs_map.items():
                if key in cs_str:
                    cs_value = val
                    break
            if cs_value is None:
                try:
                    cs_value = float(community_signal)
                except (TypeError, ValueError):
                    cs_value = None

    if cs_value is None:
        # No community signal; use download confidence as a soft proxy.
        modifier = (download_score - 50.0) * 0.1  # range -5 to +4.5
        return round(modifier, 4), f"no_community_signal_dl{download_score:.0f}"

    # Combine: high community signal + high downloads -> positive modifier.
    # Low community signal or low downloads -> negative or zero.
    signal_component = (cs_value - 50.0) * 0.1          # -5 to +5
    download_component = (download_score - 50.0) * 0.05  # -2.5 to +2.25

    modifier = signal_component + download_component
    modifier = _clamp(modifier, lo=-10.0, hi=10.0)

    return round(modifier, 4), f"cs{cs_value:.0f}_dl{download_score:.0f}"


def _score_supply_chain_risk(
    supply_chain: Any,
    age_days: Any,
) -> tuple[float, str]:
    """
    Supply_chain risk penalty for young servers with complex dependencies.

    Complex supply_chain means many dependencies -> higher risk for new servers.
    Young servers (<180 days) with complex supply chains get a penalty.
    Mature servers (365d+) with complex supply chains are more trusted.
    """
    if supply_chain is None:
        return 0.0, "no_supply_chain_data"

    # Determine complexity level
    complexity_score: float
    if isinstance(supply_chain, (int, float)):
        complexity_score = float(supply_chain)
    elif isinstance(supply_chain, str):
        sc_lower = supply_chain.lower().strip()
        complexity_map = {
            "none": 0.0,
            "minimal": 5.0,
            "simple": 10.0,
            "moderate": 30.0,
            "complex": 60.0,
            "very complex": 80.0,
            "highly complex": 95.0,
            "enterprise": 90.0,
            "single": 5.0,
            "isolated": 5.0,
            "integrated": 40.0,
            "heavy": 70.0,
        }
        found = False
        for key, val in complexity_map.items():
            if key in sc_lower:
                complexity_score = val
                found = True
                break
        if not found:
            # Try to parse numeric suffix
            try:
                complexity_score = float(supply_chain)
            except (TypeError, ValueError):
                complexity_score = 40.0  # default moderate
    else:
        complexity_score = 40.0

    # Age penalty logic
    age: float
    if age_days is None:
        age = 0.0
    else:
        try:
            age = float(age_days)
        except (TypeError, ValueError):
            age = 0.0

    # Young servers (<180d) with complex supply chains get penalised
    if age < 30:
        age_risk_multiplier = 1.2  # very young: highest risk multiplier
    elif age < 180:
        age_risk_multiplier = 1.0
    elif age < 365:
        age_risk_multiplier = 0.5
    else:
        age_risk_multiplier = 0.2  # mature servers are trusted even with complex deps

    penalty = complexity_score * age_risk_multiplier * 0.1  # max ~9.5 penalty
    penalty = _clamp(penalty, lo=0.0, hi=MAX_SCORE)

    return round(penalty, 4), f"complexity{complexity_score:.0f}_age{age:.0f}"


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compute temporal_stability score from server metadata.

    Scoring formula:
      base       = age_bracket_bonus  (0 | 15 | 25 | 35)
      modifier   = consistency_adjusted from community_signal + download_count
      penalty    = supply_chain_risk penalty for young complex servers
      final      = clamp(base + modifier - penalty, 0, 100)

    Returns:
      (score: float in [0, 100], evidence: dict)
    """
    age_days         = metadata.get("age_days")
    community_signal = metadata.get("community_signal")
    supply_chain     = metadata.get("supply_chain")
    download_count   = metadata.get("download_count")

    # Component 1: age bracket
    age_score, age_detail = _score_age_bracket(age_days), str(age_days)

    # Component 2: consistency modifier
    consistency_mod, consistency_detail = _score_consistency_modifier(
        community_signal, download_count
    )

    # Component 3: supply chain risk penalty
    sc_penalty, sc_detail = _score_supply_chain_risk(supply_chain, age_days)

    # Combine
    raw_score = age_score + consistency_mod - sc_penalty
    final_score = round(_clamp(raw_score, 0.0, MAX_SCORE), 4)

    # Evidence dict for auditability
    evidence: dict[str, Any] = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "final_score": final_score,
        "age_bracket_score": age_score,
        "age_bracket_detail": age_detail,
        "consistency_modifier": consistency_mod,
        "consistency_detail": consistency_detail,
        "supply_chain_penalty": sc_penalty,
        "supply_chain_penalty_detail": sc_detail,
        "fields_present": {
            "age_days": age_days is not None,
            "community_signal": community_signal is not None,
            "supply_chain": supply_chain is not None,
            "download_count": download_count is not None,
        },
        "raw_components": {
            "age_score": age_score,
            "consistency_mod": consistency_mod,
            "sc_penalty": sc_penalty,
            "raw_sum": round(raw_score, 4),
        },
    }

    return final_score, evidence


# ------------------------------------------------------------------
# Self-smoke (required by Gate 8 Appendix B rule 5)
# ------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    test_cases: list[tuple[str, dict[str, Any]]] = [
        # (label, metadata)
        (
            "mature_server_high_community_complex_chain",
            {
                "age_days": 400,
                "community_signal": "high",
                "supply_chain": "complex",
                "download_count": 50000,
            },
        ),
        (
            "young_server_low_community_simple_chain",
            {
                "age_days": 20,
                "community_signal": "low",
                "supply_chain": "simple",
                "download_count": 50,
            },
        ),
        (
            "mid_age_server_moderate_community_moderate_chain",
            {
                "age_days": 200,
                "community_signal": "moderate",
                "supply_chain": "moderate",
                "download_count": 5000,
            },
        ),
        (
            "very_young_server_no_community_highly_complex",
            {
                "age_days": 5,
                "community_signal": None,
                "supply_chain": "highly complex",
                "download_count": 10,
            },
        ),
        (
            "empty_metadata",
            {},
        ),
    ]

    all_passed = True
    for label, meta in test_cases:
        score, evidence = compute_score(meta)
        passed = 0.0 <= score <= MAX_SCORE
        status = "PASS" if passed else "FAIL"
        LOG.info("[%s] %s  score=%.4f  fields_present=%s", label, status, score, evidence.get("fields_present"))
        if not passed:
            all_passed = False
            LOG.error("  score out of range [0, 100]: %.4f", score)

    if all_passed:
        LOG.info("All %d self-smoke cases passed.", len(test_cases))
    else:
        LOG.error("Some self-smoke cases FAILED.")
        exit(1)
