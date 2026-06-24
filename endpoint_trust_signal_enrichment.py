# deps: 
"""
Endpoint Trust Signal Enrichment module.

Pure enrichment module exposing compute_score(metadata: dict) -> (float, dict).
Reads endpoint_trust_score, endpoint_verified, endpoint_reputation from metadata;
weighted formula (weights sum to 1.0); a missing field contributes 0 and is
appended to evidence['missing'].
Returns (score in 0..100, evidence dict with keys 'verdict' and 'missing').
No DB writes, no network, no imports of protected modules.
"""

from __future__ import annotations
from typing import Tuple, Dict, Any

# ---------------------------------------------------------------------------
# Configuration: weights (must sum to 1.0)
# ---------------------------------------------------------------------------
_WEIGHT_TRUST_SCORE = 0.5
_WEIGHT_VERIFIED = 0.2
_WEIGHT_REPUTATION = 0.3
assert abs((_WEIGHT_TRUST_SCORE + _WEIGHT_VERIFIED + _WEIGHT_REPUTATION) - 1.0) < 1e-9, (
    "endpoint_trust_signal_enrichment weights must sum to 1.0"
)

# Verdict thresholds (same convention as other enrichers)
_VERDICT_THRESHOLDS = (
    (80.0, "strong"),
    (60.0, "adequate"),
    (30.0, "weak"),
    (0.0, "insufficient"),
)

# ---------------------------------------------------------------------------
# Helper functions (pure, no I/O)
# ---------------------------------------------------------------------------

def _is_present(value: Any) -> bool:
    """Return True if a field is considered present (non‑null and non‑empty)."""
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def _score_trust(value: Any) -> float:
    """Score endpoint_trust_score.

    The raw value is expected to be numeric (0‑100). Non‑numeric or missing
    values are treated as 0.
    """
    if not _is_present(value):
        return 0.0
    try:
        num = float(value)
    except Exception:
        return 0.0
    # Clamp to 0‑100 range
    return max(0.0, min(100.0, num))


def _score_verified(value: Any) -> float:
    """Score endpoint_verified.

    Truthy values (True, "true", "yes", 1, "1") yield 100, otherwise 0.
    """
    if not _is_present(value):
        return 0.0
    if isinstance(value, bool):
        return 100.0 if value else 0.0
    if isinstance(value, (int, float)):
        return 100.0 if value == 1 else 0.0
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "yes", "1", "y", "t"}:
            return 100.0
        if token in {"false", "no", "0", "n", "f"}:
            return 0.0
    return 0.0


def _score_reputation(value: Any) -> float:
    """Score endpoint_reputation.

    Expected numeric (0‑100). Non‑numeric values are treated as 0.
    """
    if not _is_present(value):
        return 0.0
    try:
        num = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(100.0, num))


def _verdict_for(score: float) -> str:
    """Map a 0‑100 score to a discrete verdict string."""
    for threshold, label in _VERDICT_THRESHOLDS:
        if score >= threshold:
            return label
    return "insufficient"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_score(metadata: dict) -> Tuple[float, Dict[str, Any]]:
    """Compute the endpoint trust enrichment score.

    Args:
        metadata: dict possibly containing any of:
            - endpoint_trust_score
            - endpoint_verified
            - endpoint_reputation

    Returns:
        (score, evidence) where:
            score    -- float in [0.0, 100.0]
            evidence -- dict with at least keys 'verdict' and 'missing'.
    """
    if not isinstance(metadata, dict):
        metadata = {}

    missing = []
    # Compute sub‑scores
    trust_raw = metadata.get("endpoint_trust_score")
    verified_raw = metadata.get("endpoint_verified")
    reputation_raw = metadata.get("endpoint_reputation")

    trust_score = _score_trust(trust_raw)
    verified_score = _score_verified(verified_raw)
    reputation_score = _score_reputation(reputation_raw)

    # Track missing fields
    if not _is_present(trust_raw):
        missing.append("endpoint_trust_score")
    if not _is_present(verified_raw):
        missing.append("endpoint_verified")
    if not _is_present(reputation_raw):
        missing.append("endpoint_reputation")

    # Weighted sum (missing fields contribute 0)
    weighted_sum = (
        _WEIGHT_TRUST_SCORE * trust_score
        + _WEIGHT_VERIFIED * verified_score
        + _WEIGHT_REPUTATION * reputation_score
    )

    # Clamp final score
    score = round(max(0.0, min(100.0, weighted_sum)), 2)

    evidence: Dict[str, Any] = {
        "verdict": _verdict_for(score),
        "missing": missing,
    }
    return score, evidence

# ---------------------------------------------------------------------------
# Self‑test harness
# ---------------------------------------------------------------------------

def _run_self_test() -> int:
    """Run a few sanity checks; return 0 on success, non‑zero on failure."""
    # 1. Only trust score provided
    s1, e1 = compute_score({"endpoint_trust_score": 95})
    assert 0.0 <= s1 <= 100.0, "score out of range"
    assert "verdict" in e1 and isinstance(e1["verdict"], str)
    assert "missing" in e1 and isinstance(e1["missing"], list)
    assert "endpoint_verified" in e1["missing"]
    assert "endpoint_reputation" in e1["missing"]

    # 2. All fields present, strong values
    s2, e2 = compute_score({
        "endpoint_trust_score": 80,
        "endpoint_verified": True,
        "endpoint_reputation": 90,
    })
    assert 0.0 <= s2 <= 100.0
    assert e2["missing"] == []
    # Expected score: 0.5*80 + 0.2*100 + 0.3*90 = 40 + 20 + 27 = 87
    assert abs(s2 - 87.0) < 1e-6, f"unexpected score {s2}"
    assert e2["verdict"] == "strong"

    # 3. No fields provided
    s3, e3 = compute_score({})
    assert s3 == 0.0
    assert set(e3["missing"]) == {"endpoint_trust_score", "endpoint_verified", "endpoint_reputation"}
    assert e3["verdict"] == "insufficient"

    print("PASS endpoint_trust_signal_enrichment")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(_run_self_test())
