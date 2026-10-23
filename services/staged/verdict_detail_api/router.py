# services/staged/verdict_detail_api/logic.py
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import McpLlmAxisScore, McpServerRegistry


def get_trust_gating_override(server_id: int, trust_score: float, current_risk_tier: str) -> tuple[Optional[str], bool]:
    """Apply trust gating override logic.
    
    Returns (published_overall_risk_override, is_trusted)
    """
    is_trusted = trust_score is not None and trust_score >= 80.0
    if is_trusted:
        return None, True
    return None, False


def compute_risk_tier(
    overall_risk: Optional[str],
    axes: list[dict],
    trust_override: Optional[str],
    is_trusted: bool
) -> str:
    """Compute the effective risk tier based on axes and trust override."""
    if trust_override:
        return trust_override
    
    if overall_risk in ("CRITICAL", "HIGH_RISK_ISOLATED"):
        return overall_risk
    
    max_severity = "SAFE"
    severity_order = {"SAFE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4, "HIGH_RISK_ISOLATED": 5}
    
    for axis in axes:
        if axis.get("escalated"):
            return "HIGH_RISK_ISOLATED"
        label = axis.get("label", "").upper()
        if label in severity_order and severity_order[label] > severity_order.get(max_severity, 0):
            if label in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "SAFE"):
                max_severity = label
    
    if max_severity == "CRITICAL":
        return "CRITICAL"
    elif max_severity == "HIGH":
        return "HIGH_RISK_ISOLATED"
    
    return max_severity


def get_verdict_detail(
    session: Session,
    server_id: int
) -> Optional[dict]:
    """Get detailed verdict for a server including all axis scores."""
    stmt = (
        select(McpServerRegistry, McpLlmAxisScore)
        .join(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id
        )
        .where(McpServerRegistry.server_id == server_id)
    )
    
    result = session.execute(stmt).fetchall()
    
    if not result:
        return None
    
    server_row = result[0][0]
    axes_data = []
    
    for server_obj, axis_obj in result:
        axes_data.append({
            "axis_name": axis_obj.axis_name,
            "label": axis_obj.label,
            "label_index": axis_obj.label_index,
            "p_top": axis_obj.p_top,
            "p_critical": axis_obj.p_critical,
            "p_danger": axis_obj.p_danger,
            "escalated": axis_obj.escalated,
        })
    
    trust_score = server_row.trust_score
    current_risk_tier = server_row.risk_tier or "UNKNOWN"
    published_override, is_trusted = get_trust_gating_override(server_id, trust_score, current_risk_tier)
    
    overall_risk = server_row.verdict
    if published_override:
        effective_risk = published_override
    else:
        effective_risk = overall_risk
    
    risk_tier = compute_risk_tier(overall_risk, axes_data, published_override, is_trusted)
    
    axes_dict = {a["axis_name"]: a for a in axes_data}
    
    return {
        "server_id": server_id,
        "server_name": server_row.name,
        "axes": axes_dict,
        "overall": overall_risk,
        "published_overall_risk": effective_risk,
        "trusted": is_trusted,
        "risk_tier": risk_tier,
        "verdict_reasoning": server_row.verdict_reasoning,
        "last_assessed": server_row.last_assessed,
        "criteria_version": server_row.meta.get("criteria_version") if server_row.meta else None,
    }