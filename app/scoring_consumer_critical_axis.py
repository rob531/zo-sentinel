# deps: fastapi
"""
scoring_consumer_critical_axis.py
Signal: critical-axis escalation scorer

Detects servers where at least one risk axis (auth_strength, capability_breadth,
data_sensitivity, network_egress, maintainer_trust, exploit_surface) has p_critical
elevated above threshold and forces an elevated risk tier.

CONTRACT (PRODUCT_SPEC §3 enricher):
  compute_score(metadata: dict) -> tuple[float, dict]
  Pure function: no DB writes, no network, no file I/O.
  Returns (escalation_score in [0,100], evidence dict with signal_type,
           confidence, evidence_blob, escalated bool).
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

# Three-tier escalation based on p_critical probability.
# p_critical > 0.70  -> escalation_score = 100  (CRITICAL tier)
# p_critical 0.30-0.70 -> escalation_score = 50   (elevated/moderate)
# p_critical < 0.30  -> escalation_score = 0     (no escalation)


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute escalation score for a server axis based on p_critical.

    Args:
        metadata: dict with keys:
            - server_id (str): MCP server identifier
            - axis_name (str): one of the 6 risk axes
            - p_critical (float): probability of CRITICAL label [0.0, 1.0]

    Returns:
        Tuple of (escalation_score, evidence dict):
            escalation_score: 0.0 | 50.0 | 100.0
            evidence:
                signal_type: "critical_axis_escalation"
                confidence: float 0-1 reflecting certainty of the tier boundary
                evidence_blob: dict with axis_name, p_critical, tier, reason
                escalated: bool  (True when escalation_score > 0)
    """
    server_id = str(metadata.get("server_id", ""))
    axis_name = str(metadata.get("axis_name", ""))
    p_critical = float(metadata.get("p_critical", 0.0))

    # Clamp to [0, 1]
    p_critical = max(0.0, min(1.0, p_critical))

    if p_critical > 0.70:
        escalation_score = 100.0
        tier = "CRITICAL"
        confidence = min(1.0, (p_critical - 0.70) / 0.30 + 0.8)
        reason = f"p_critical={p_critical:.3f} exceeds 0.70 threshold"
        escalated = True
    elif p_critical >= 0.30:
        escalation_score = 50.0
        tier = "ELEVATED"
        confidence = min(1.0, (p_critical - 0.30) / 0.40 + 0.5)
        reason = f"p_critical={p_critical:.3f} in [0.30, 0.70] range"
        escalated = False
    else:
        escalation_score = 0.0
        tier = "NOMINAL"
        confidence = 0.5 + (0.30 - p_critical) / 0.30 * 0.5
        reason = f"p_critical={p_critical:.3f} below 0.30 threshold"
        escalated = False

    evidence_blob = {
        "server_id": server_id,
        "axis_name": axis_name,
        "p_critical": round(p_critical, 4),
        "tier": tier,
        "reason": reason,
    }

    evidence = {
        "signal_type": "critical_axis_escalation",
        "confidence": round(confidence, 4),
        "evidence_blob": evidence_blob,
        "escalated": escalated,
    }

    return escalation_score, evidence


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Seed dicts with known p_critical values; assert expected scores.
    cases = [
        {"server_id": "srv_high", "axis_name": "exploit_surface", "p_critical": 0.8},
        {"server_id": "srv_mid",  "axis_name": "data_sensitivity", "p_critical": 0.5},
        {"server_id": "srv_low",  "axis_name": "auth_strength",    "p_critical": 0.1},
    ]
    expected_scores = [100.0, 50.0, 0.0]

    for metadata, want in zip(cases, expected_scores):
        score, evidence = compute_score(metadata)
        assert score == want, f"{metadata['server_id']}: score={score}, want={want}"
        assert "signal_type" in evidence, f"missing signal_type in {evidence}"
        assert "confidence" in evidence, f"missing confidence in {evidence}"
        assert "evidence_blob" in evidence, f"missing evidence_blob in {evidence}"
        assert "escalated" in evidence, f"missing escalated in {evidence}"
        # escalated True only for CRITICAL tier (score 100)
        assert evidence["escalated"] == (want == 100.0), (
            f"{metadata['server_id']}: escalated={evidence['escalated']}, want={want == 100.0}"
        )
        assert evidence["evidence_blob"]["axis_name"] == metadata["axis_name"]
        assert evidence["evidence_blob"]["p_critical"] == metadata["p_critical"]

    # Edge cases
    score_0, ev_0 = compute_score({"server_id": "x", "axis_name": "y", "p_critical": 0.0})
    assert score_0 == 0.0, score_0
    assert ev_0["evidence_blob"]["tier"] == "NOMINAL", ev_0

    score_1, ev_1 = compute_score({"server_id": "x", "axis_name": "y", "p_critical": 1.0})
    assert score_1 == 100.0, score_1
    assert ev_1["escalated"] is True, ev_1

    # Missing keys use defaults
    score_na, ev_na = compute_score({})
    assert score_na == 0.0, score_na
    assert ev_na["confidence"] > 0.0, ev_na

    print("PASS")
