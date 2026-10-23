"""Risk tier aggregation logic layer."""
from typing import Any
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import McpLlmAxisScore, McpServerRegistry

# Tier thresholds
TRUSTED_GENERAL = 75
TRUSTED_RESEARCH = 60
ENTERPRISE_CONTROLLED = 45
CAUTION_LIMITED = 30
HIGH_RISK_ISOLATED = 15

AXIS_NAMES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

CRITERIA_VERSION = "1.0.0"


def _compute_composite_score(axis_scores: list[dict]) -> float:
    """Compute composite score from axis scores."""
    if not axis_scores:
        return 0.0
    # Weight overall_risk heavily; others contribute equally
    weights = {
        "overall_risk": 0.4,
        "auth_strength": 0.1,
        "capability_breadth": 0.1,
        "data_sensitivity": 0.1,
        "network_egress": 0.1,
        "maintainer_trust": 0.1,
        "exploit_surface": 0.1,
    }
    total = 0.0
    for score in axis_scores:
        axis = score.get("axis_name", "")
        p_top = score.get("p_top", 0.0)
        weight = weights.get(axis, 0.1)
        total += p_top * weight
    return total * 100


def _determine_tier(composite_score: float) -> str:
    """Determine risk tier from composite score."""
    if composite_score > TRUSTED_GENERAL:
        return "TRUSTED_GENERAL"
    elif composite_score > TRUSTED_RESEARCH:
        return "TRUSTED_RESEARCH"
    elif composite_score > ENTERPRISE_CONTROLLED:
        return "ENTERPRISE_CONTROLLED"
    elif composite_score > CAUTION_LIMITED:
        return "CAUTION_LIMITED"
    elif composite_score > HIGH_RISK_ISOLATED:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"


def _check_trusted_override(
    session: Session, server_id: str, url: str | None, name: str | None
) -> bool:
    """Check if trust_gate returns true for this server."""
    try:
        from trust_gating_override import trust_gate
    except ImportError:
        return False
    
    if not url or not name:
        return False
    
    result = trust_gate(url, name, {"server_id": server_id})
    return bool(result)


def get_risk_tier_data(
    session: Session, server_id: str
) -> dict[str, Any] | None:
    """Get risk tier data for a server."""
    # Get server info
    server_stmt = select(McpServerRegistry).where(
        McpServerRegistry.server_id == server_id
    )
    server = session.execute(server_stmt).scalar_one_or_none()
    if not server:
        return None
    
    # Get all axis scores for this server
    scores_stmt = select(McpLlmAxisScore).where(
        McpLlmAxisScore.server_id == server_id
    )
    scores = session.execute(scores_stmt).scalars().all()
    
    # Filter to our 7 axes
    axis_scores = [
        {
            "axis_name": s.axis_name,
            "label": s.label,
            "p_top": s.p_top or 0.0,
            "p_critical": s.p_critical or 0.0,
            "p_danger": s.p_danger or 0.0,
            "probs": s.probs,
        }
        for s in scores
        if s.axis_name in AXIS_NAMES
    ]
    
    composite_score = _compute_composite_score(axis_scores)
    risk_tier = _determine_tier(composite_score)
    
    trusted_override = _check_trusted_override(
        session, server_id, server.url, server.name
    )
    
    # Format axes as dict
    axes_dict = {}
    for score in axis_scores:
        axes_dict[score["axis_name"]] = {
            "label": score["label"],
            "p_top": score["p_top"],
            "p_critical": score["p_critical"],
            "p_danger": score["p_danger"],
            "probs": score["probs"],
        }
    
    return {
        "server_id": server_id,
        "name": server.name,
        "axes": axes_dict,
        "composite_score": composite_score,
        "risk_tier": risk_tier,
        "trusted_override": trusted_override,
        "criteria_version": CRITERIA_VERSION,
        "last_assessed": server.last_assessed,
    }