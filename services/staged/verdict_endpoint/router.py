# services/staged/verdict_endpoint/logic.py
from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import McpServerRegistry, McpLlmAxisScore

RISK_TIER_DEFAULT = "MEDIUM_RISK"
RISK_TIER_OVERRIDE = "HIGH_RISK_ISOLATED"
CRITERIA_VERSION = "v1"


def compute_risk_tier(axes: List[dict]) -> str:
    for axis in axes:
        if axis.get("p_critical", 0) >= 0.5:
            return RISK_TIER_OVERRIDE
    return RISK_TIER_DEFAULT


def get_server_verdict_data(session: Session, server_id: str) -> Optional[dict]:
    stmt_server = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    server = session.execute(stmt_server).scalar_one_or_none()
    if not server:
        return None

    stmt_scores = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    scores = list(session.execute(stmt_scores).scalars().all())

    axes = []
    for score in scores:
        axes.append({
            "axis_name": score.axis_name,
            "label": score.label,
            "label_index": score.label_index,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "escalated": score.escalated,
        })

    axes.sort(key=lambda x: x["label_index"] if x["label_index"] is not None else 0)
    risk_tier = compute_risk_tier(axes)

    scored_at = None
    for score in scores:
        if score.scored_at:
            scored_at = score.scored_at
            break

    return {
        "server_id": server.server_id,
        "name": server.name,
        "verdict": server.verdict,
        "risk_tier": risk_tier,
        "axes": axes,
        "criteria_version": CRITERIA_VERSION,
        "scored_at": scored_at,
    }


def get_verdict_summary_data(session: Session, server_id: str) -> Optional[dict]:
    verdict = get_server_verdict_data(session, server_id)
    if not verdict:
        return None
    return {
        "server_id": verdict["server_id"],
        "name": verdict["name"],
        "verdict": verdict["verdict"],
        "risk_tier": verdict["risk_tier"],
        "criteria_version": verdict["criteria_version"],
    }