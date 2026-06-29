# verdict_view_api.py
"""
FastAPI router that exposes the verdict for a given server.

The endpoint aggregates the per‑axis LLM scores produced by the
`app_scoring_consumer` service and enriches the response with the
trust‑gate override information.

Response schema (example):
{
    "server_id": "abc123",
    "criteria_version": "v2",
    "overall_risk": 0.63,
    "risk_tier": "medium",
    "trust_gate_override": {"layer": "none"},
    "axis_breakdown": [
        {"label": "auth_strength", "p_top": 0.71},
        {"label": "capability_breadth", "p_top": 0.58},
        {"label": "data_sensitivity", "p_top": 0.44},
        {"label": "network_egress", "p_top": 0.62},
        {"label": "maintainer_trust", "p_top": 0.79},
        {"label": "exploit_surface", "p_top": 0.55}
    ]
}
"""

from __future__ import annotations

from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, status

# --------------------------------------------------------------------------- #
# Imports from the local application – these modules already exist in the repo.
# --------------------------------------------------------------------------- #
# `app_scoring_consumer` provides a callable that returns the raw LLM axis
# scores for a given server identifier.
# `trust_gating_override` provides a callable that returns any active override
# for the server (e.g., a manual risk tier that should supersede the computed
# one).
# --------------------------------------------------------------------------- #
from app_scoring_consumer import get_mcp_llm_axis_scores
from trust_gating_override import get_trust_gate_override

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helper utilities
# --------------------------------------------------------------------------- #
def _derive_overall_risk(axis_scores: List[float]) -> float:
    """
    Derive an overall risk value from the per‑axis probabilities.

    The implementation uses a simple arithmetic mean – this mirrors the
    behaviour of the original prototype and keeps the calculation deterministic.
    """
    if not axis_scores:
        return 0.0
    return sum(axis_scores) / len(axis_scores)


def _risk_tier_from_overall(overall: float) -> str:
    """
    Convert an overall risk float (0‑1) into a human‑readable tier.

    Thresholds are:
        - high   : overall >= 0.80
        - medium : 0.50 <= overall < 0.80
        - low    : overall < 0.50
    """
    if overall >= 0.80:
        return "high"
    if overall >= 0.50:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/verdict",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Retrieve the risk verdict for a server",
    tags=["verdict"],
)
async def get_server_verdict(server_id: str) -> Dict[str, Any]:
    """
    Return a detailed risk verdict for the requested server.

    The response contains:
        * `criteria_version` – version string supplied by the scoring service.
        * `overall_risk` – numeric aggregate (0‑1) of the six axis scores.
        * `risk_tier` – derived tier string (high / medium / low).
        * `trust_gate_override` – any manual override layer (may be empty).
        * `axis_breakdown` – list of `{label, p_top}` for each of the six axes.
    """
    # ------------------------------------------------------------------- #
    # 1️⃣  Pull the raw axis scores from the scoring consumer.
    # ------------------------------------------------------------------- #
    try:
        raw_scores = await get_mcp_llm_axis_scores(server_id)
    except Exception as exc:
        # The consumer raises its own exception types; we normalise to a 404.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scoring data not found for server_id={server_id}",
        ) from exc

    # Expected shape (example):
    # {
    #     "criteria_version": "v2",
    #     "auth_strength": {"p_top": 0.71},
    #     "capability_breadth": {"p_top": 0.58},
    #     "data_sensitivity": {"p_top": 0.44},
    #     "network_egress": {"p_top": 0.62},
    #     "maintainer_trust": {"p_top": 0.79},
    #     "exploit_surface": {"p_top": 0.55}
    # }
    # ------------------------------------------------------------------- #

    # ------------------------------------------------------------------- #
    # 2️⃣  Validate that all required axes are present.
    # ------------------------------------------------------------------- #
    required_axes = [
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]

    missing = [axis for axis in required_axes if axis not in raw_scores]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Missing axis scores for server_id={server_id}: {', '.join(missing)}",
        )

    # ------------------------------------------------------------------- #
    # 3️⃣  Build the per‑axis breakdown and collect the numeric values.
    # ------------------------------------------------------------------- #
    axis_breakdown: List[Dict[str, Any]] = []
    axis_values: List[float] = []

    for axis in required_axes:
        axis_entry = raw_scores[axis]
        # Defensive: accept either a raw float or a dict with a `p_top` key.
        if isinstance(axis_entry, dict):
            p_top = axis_entry.get("p_top")
        else:
            p_top = axis_entry

        if p_top is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Axis '{axis}' missing 'p_top' value for server_id={server_id}",
            )

        # Ensure the value is a float between 0 and 1.
        try:
            p_top_float = float(p_top)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Invalid p_top for axis '{axis}' on server_id={server_id}",
            ) from exc

        if not (0.0 <= p_top_float <= 1.0):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"p_top out of range for axis '{axis}' on server_id={server_id}",
            )

        axis_breakdown.append({"label": axis, "p_top": p_top_float})
        axis_values.append(p_top_float)

    # ------------------------------------------------------------------- #
    # 4️⃣  Derive the overall risk and tier.
    # ------------------------------------------------------------------- #
    overall_risk = _derive_overall_risk(axis_values)
    risk_tier = _risk_tier_from_overall(overall_risk)

    # ------------------------------------------------------------------- #
    # 5️⃣  Pull any trust‑gate override information.
    # ------------------------------------------------------------------- #
    # The override function returns a dict (or None) describing the manual
    # layer that should be applied.  If no override exists we return an empty
    # dict to keep the response shape stable.
    try:
        override_info = await get_trust_gate_override(server_id)
    except Exception:
        # If the override service fails we do not want the whole endpoint to
        # break – we log the failure (the logger is provided by FastAPI's
        # standard logger) and continue with an empty dict.
        import logging

        logging.getLogger(__name__).exception(
            "Failed to retrieve trust gate override for server_id=%s", server_id
        )
        override_info = {}

    if not isinstance(override_info, dict):
        # Normalise unexpected return types.
        override_info = {}

    # ------------------------------------------------------------------- #
    # 6️⃣  Assemble the final payload.
    # ------------------------------------------------------------------- #
    response: Dict[str, Any] = {
        "server_id": server_id,
        "criteria_version": raw_scores.get("criteria_version", "unknown"),
        "overall_risk": round(overall_risk, 4),
        "risk_tier": risk_tier,
        "trust_gate_override": override_info,
        "axis_breakdown": axis_breakdown,
    }

    return response