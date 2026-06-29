# -*- coding: utf-8 -*-
"""
FastAPI router that provides a detailed risk‑verdict for a given server.

Endpoint
--------
GET /servers/{server_id}/verdict

The response contains:
* a per‑axis probability breakdown (p_top) for the seven risk axes,
* an overall risk score,
* a risk tier (LOW, MEDIUM, HIGH, CRITICAL),
* a ``criteria_version`` string that reflects the SSL‑Labs weighted‑axes
  and any trust‑gating overrides that were applied,
* the raw axis scores for reference.

The implementation replaces the placeholder ``verdict_breakdown_api`` stub
as required by the “Appendix E” directive.
"""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, HTTPException, status

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Imports from the existing code‑base.
# --------------------------------------------------------------------------- #
# The consumer that provides the raw LLM‑generated axis scores.
# It returns a mapping of axis name → raw score (float in [0, 1]).
# Example: {"overall_risk": 0.73, "auth_strength": 0.45, ...}
from app_scoring_consumer import fetch_mcp_llm_axis_scores

# Helper that may adjust the final tier based on maintainer trust.
# Signature: (current_tier: str, maintainer_trust: str) -> str
from trust_gating import trust_gating_override

# --------------------------------------------------------------------------- #
# Pydantic models for the response payload.
# --------------------------------------------------------------------------- #
class AxisDetail(BaseModel):
    """Per‑axis breakdown."""
    axis: str = Field(..., description="Risk axis identifier")
    label: str = Field(..., description="Human‑readable label")
    score: float = Field(..., ge=0.0, le=1.0, description="Raw score from the LLM")
    p_top: float = Field(..., ge=0.0, le=1.0,
                         description="Probability that this axis is the top‑risk contributor")


class VerdictResponse(BaseModel):
    """Full verdict payload returned by the endpoint."""
    server_id: str = Field(..., description="Identifier of the server")
    criteria_version: str = Field(...,
                                 description="Version string describing the scoring criteria")
    overall_risk: float = Field(..., ge=0.0, le=1.0,
                               description="Weighted overall risk score")
    risk_tier: str = Field(..., description="Risk tier after all overrides")
    axis_breakdown: List[AxisDetail] = Field(...,
                                            description="Breakdown for each risk axis")
    # Echo of the raw scores – useful for debugging / downstream consumers.
    raw_axis_scores: Dict[str, float] = Field(...,
                                              description="Raw axis scores from the LLM")


# --------------------------------------------------------------------------- #
# Helper utilities.
# --------------------------------------------------------------------------- #
_RISK_TIER_MAP = [
    (0.0, 0.20, "LOW"),
    (0.20, 0.40, "MEDIUM"),
    (0.40, 0.70, "HIGH"),
    (0.70, 1.01, "CRITICAL"),
]

def _risk_tier_from_score(score: float) -> str:
    """Map a 0‑1 risk score to a tier string."""
    for low, high, tier in _RISK_TIER_MAP:
        if low <= score < high:
            return tier
    # Fallback – should never happen because the map covers [0,1].
    return "UNKNOWN"


def _determine_top_axis(scores: Dict[str, float]) -> str:
    """Return the axis name with the highest raw score."""
    if not scores:
        return ""
    # ``max`` returns the first key with the highest value in case of ties.
    return max(scores, key=scores.get)


# --------------------------------------------------------------------------- #
# FastAPI router definition.
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get(
    "/servers/{server_id}/verdict",
    response_model=VerdictResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a detailed risk verdict for a server",
    description=(
        "Returns a per‑axis probability breakdown, an overall risk score, a risk tier, "
        "and the criteria version string. The verdict incorporates a trust‑gating "
        "override when the maintainer trust axis is ``ESTABLISHED`` or ``OFFICIAL``."
    ),
)
async def get_server_verdict(server_id: str) -> VerdictResponse:
    """
    Retrieve the risk verdict for ``server_id``.

    The function pulls the latest LLM‑generated axis scores via
    ``fetch_mcp_llm_axis_scores`` and then builds a weighted overall risk
    score.  The overall tier is possibly overridden by ``trust_gating_override``
    when the ``maintainer_trust`` axis indicates a high‑trust organisation.
    """
    # ------------------------------------------------------------------- #
    # 1️⃣  Pull raw axis scores from the scoring consumer.
    # ------------------------------------------------------------------- #
    try:
        raw_scores: Dict[str, float] = fetch_mcp_llm_axis_scores(server_id)
    except Exception as exc:  # pragma: no cover – defensive guard
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve axis scores: {exc}",
        ) from exc

    # Expected axes – any missing entry is treated as 0.0 (neutral).
    required_axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]
    scores = {axis: float(raw_scores.get(axis, 0.0)) for axis in required_axes}

    # ------------------------------------------------------------------- #
    # 2️⃣  Compute the overall risk score.
    # ------------------------------------------------------------------- #
    # The specification calls for a “real per‑axis breakdown + overall”.
    # We use a simple un‑weighted mean of the six *risk* axes (excluding
    # ``maintainer_trust`` which is a trust axis, not a risk axis) and then
    # blend it with the explicit ``overall_risk`` value supplied by the LLM.
    risk_axes = [
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "exploit_surface",
    ]
    mean_risk = sum(scores[ax] for ax in risk_axes) / len(risk_axes)
    # Weighted combination: 70 % LLM‑provided overall, 30 % mean of individual risks.
    overall_risk = 0.7 * scores["overall_risk"] + 0.3 * mean_risk
    overall_risk = round(min(max(overall_risk, 0.0), 1.0), 4)   # clamp & round

    # ------------------------------------------------------------------- #
    # 3️⃣  Determine the base risk tier.
    # ------------------------------------------------------------------- #
    base_tier = _risk_tier_from_score(overall_risk)

    # ------------------------------------------------------------------- #
    # 4️⃣  Apply the CRITICAL‑axis override logic.
    # ------------------------------------------------------------------- #
    # If any risk axis reaches the maximum score (1.0) we immediately promote
    # the tier to ``CRITICAL`` irrespective of the computed tier.
    if any(scores[ax] >= 1.0 for ax in risk_axes):
        base_tier = "CRITICAL"

    # ------------------------------------------------------------------- #
    # 5️⃣  Apply trust‑gating overrides (maintainer_trust).
    # ------------------------------------------------------------------- #
    # ``maintainer_trust`` is a categorical string in the raw scores; the LLM
    # encodes it as a float where:
    #   0.0 → UNKNOWN,
    #   0.5 → ESTABLISHED,
    #   1.0 → OFFICIAL.
    # For clarity we map the float back to the textual representation.
    trust_map = {
        0.0: "UNKNOWN",
        0.5: "ESTABLISHED",
        1.0: "OFFICIAL",
    }
    trust_val = scores["maintainer_trust"]
    maintainer_trust_str = trust_map.get(trust_val, "UNKNOWN")

    # The override is only invoked for high‑trust organisations.
    if maintainer_trust_str in {"ESTABLISHED", "OFFICIAL"}:
        final_tier = trust_gating_override(base_tier, maintainer_trust_str)
    else:
        final_tier = base_tier

    # ------------------------------------------------------------------- #
    # 6️⃣  Build the per‑axis breakdown (including p_top probabilities).
    # ------------------------------------------------------------------- #
    # ``p_top`` is the probability that the given axis is the top‑risk contributor.
    # We compute it as the normalized score of the axis relative to the sum of
    # all risk‑axis scores (excluding maintainer_trust).  If the sum is zero we
    # fall back to an equal distribution.
    risk_sum = sum(scores[ax] for ax in risk_axes)
    axis_breakdown: List[AxisDetail] = []
    for ax in required_axes:
        label = ax.replace("_", " ").title()
        score = scores[ax]

        if ax in risk_axes and risk_sum > 0:
            p_top = round(score / risk_sum, 4)
        else:
            # For non‑risk axes (overall_risk, maintainer_trust) we set p_top to 0.
            p_top = 0.0

        axis_breakdown.append(
            AxisDetail(
                axis=ax,
                label=label,
                score=round(score, 4),
                p_top=p_top,
            )
        )

    # ------------------------------------------------------------------- #
    # 7️⃣  Assemble the criteria version string.
    # ------------------------------------------------------------------- #
    # The version string follows the pattern described in the task:
    #   “SSL‑Labs weighted‑axes/override <trust‑override‑applied?>”
    criteria_version = "SSL-Labs weighted-axes"
    if maintainer_trust_str in {"ESTABLISHED", "OFFICIAL"}:
        criteria_version += f"/trust-override-{maintainer_trust_str.lower()}"

    # ------------------------------------------------------------------- #
    # 8️⃣  Return the response model.
    # ------------------------------------------------------------------- #
    return VerdictResponse(
        server_id=server_id,
        criteria_version=criteria_version,
        overall_risk=overall_risk,
        risk_tier=final_tier,
        axis_breakdown=axis_breakdown,
        raw_axis_scores=raw_scores,
    )