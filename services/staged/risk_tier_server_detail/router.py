# services/staged/risk_tier_server_detail/logic.py
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from .schemas import (
    RiskAxisResponse,
    OverallRiskResponse,
    ServerRiskDetailResponse,
)


def get_risk_tier_score(tier: str) -> int:
    """Convert risk tier string to numeric score for comparison."""
    tier_scores = {
        "critical": 5,
        "high": 4,
        "medium": 3,
        "low": 2,
        "minimal": 1,
        "unknown": 0,
    }
    return tier_scores.get(tier.lower(), 0)


def get_risk_tier(p_top: float, p_critical: float, p_danger: float) -> str:
    """Determine risk tier based on probability scores."""
    if p_top >= 0.5:
        return "critical"
    elif p_critical >= 0.6:
        return "high"
    elif p_danger >= 0.7:
        return "medium"
    elif p_danger >= 0.4:
        return "low"
    elif p_danger >= 0.2:
        return "minimal"
    return "unknown"


async def fetch_server_risk_detail(
    session: Session,
    server_id: str,
) -> Optional[ServerRiskDetailResponse]:
    """
    Fetch server risk detail including all 6 risk axes + overall risk.
    Joins McpLlmAxisScore with McpServerRegistry for server name.
    """
    # First get server name from registry
    server_query = text("""
        SELECT name 
        FROM McpServerRegistry 
        WHERE id = :server_id
    """)
    server_result = session.execute(server_query, {"server_id": server_id})
    server_row = server_result.fetchone()
    
    if not server_row:
        return None
    
    server_name = server_row[0]
    
    # Query all axis scores for this server (6 axes + overall)
    axes_query = text("""
        SELECT 
            a.axis_name,
            a.label,
            a.p_top,
            a.p_critical,
            a.p_danger,
            a.escalated,
            a.risk_tier,
            a.criteria_version,
            a.last_scored,
            a.server_id
        FROM McpLlmAxisScore a
        WHERE a.server_id = :server_id
        ORDER BY 
            CASE a.axis_name 
                WHEN 'overall_risk' THEN 0 
                ELSE 1 
            END,
            a.axis_name
    """)
    
    result = session.execute(axes_query, {"server_id": server_id})
    rows = result.fetchall()
    
    if not rows:
        return None
    
    axes: List[RiskAxisResponse] = []
    overall: Optional[OverallRiskResponse] = None
    final_risk_tier = "unknown"
    criteria_version = None
    last_scored = None
    
    for row in rows:
        axis_name = row[0]
        label = row[1]
        p_top = float(row[2]) if row[2] is not None else 0.0
        p_critical = float(row[3]) if row[3] is not None else 0.0
        p_danger = float(row[4]) if row[4] is not None else 0.0
        escalated = bool(row[5]) if row[5] is not None else False
        risk_tier = row[6]
        criteria_version = row[7]
        last_scored = row[8]
        
        if axis_name == "overall_risk":
            overall = OverallRiskResponse(
                label=label,
                p_top=p_top,
                p_critical=p_critical,
                p_danger=p_danger,
            )
            final_risk_tier = risk_tier or get_risk_tier(p_top, p_critical, p_danger)
        else:
            axes.append(RiskAxisResponse(
                axis_name=axis_name,
                label=label,
                p_top=p_top,
                p_critical=p_critical,
                p_danger=p_danger,
                escalated=escalated,
            ))
    
    return ServerRiskDetailResponse(
        server_id=server_id,
        server_name=server_name,
        axes=axes,
        overall=overall,
        risk_tier=final_risk_tier,
        criteria_version=criteria_version,
        last_scored=last_scored,
    )