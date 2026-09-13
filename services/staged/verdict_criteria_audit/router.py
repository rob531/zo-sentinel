from datetime import datetime, timedelta
from typing import Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from pydantic import BaseModel
from sqlalchemy import and_, text
from sqlalchemy.orm import Session


class ServerCriteriaAudit(BaseModel):
    server_id: str
    server_name: str
    overall_risk: str
    risk_tier: str
    critical_axes: list[str]
    criteria_version: str
    last_assessed: Optional[datetime]


class CriteriaAuditResult(BaseModel):
    servers: list[ServerCriteriaAudit]


def audit_verdict_criteria(
    session: Session,
    days: int = 30,
    p_critical_threshold: float = 0.5,
) -> CriteriaAuditResult:
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # Get servers with their latest assessment info
    servers_query = text("""
        SELECT DISTINCT ON (s.server_id)
            s.server_id,
            s.name as server_name,
            s.verdict as overall_risk,
            s.risk_tier,
            s.decision_rule_version as criteria_version,
            s.last_assessed
        FROM mcp_server_registry s
        ORDER BY s.server_id, s.last_assessed DESC NULLS LAST
    """)
    
    servers_result = session.execute(servers_query)
    servers_data = {row.server_id: {
        "server_id": row.server_id,
        "server_name": row.server_name,
        "overall_risk": row.overall_risk,
        "risk_tier": row.risk_tier,
        "criteria_version": row.criteria_version or "unknown",
        "last_assessed": row.last_assessed,
    } for row in servers_result}
    
    # Get critical axes for each server
    critical_axes_query = text("""
        SELECT 
            ax.server_id,
            ax.axis_name,
            ax.p_critical,
            ax.decision_rule_version
        FROM mcp_llm_axis_scores ax
        WHERE ax.p_critical > :threshold
        AND ax.scored_at >= :cutoff
        ORDER BY ax.server_id, ax.p_critical DESC
    """)
    
    critical_result = session.execute(
        critical_axes_query,
        {"threshold": p_critical_threshold, "cutoff": cutoff}
    )
    
    # Group critical axes by server
    critical_by_server: dict[str, list[str]] = {}
    for row in critical_result:
        if row.server_id not in critical_by_server:
            critical_by_server[row.server_id] = []
        if row.axis_name not in critical_by_server[row.server_id]:
            critical_by_server[row.server_id].append(row.axis_name)
    
    # Build final result
    servers_list = []
    for server_id, server_info in servers_data.items():
        servers_list.append(ServerCriteriaAudit(
            server_id=server_info["server_id"],
            server_name=server_info["server_name"],
            overall_risk=server_info["overall_risk"] or "unknown",
            risk_tier=server_info["risk_tier"] or "unknown",
            critical_axes=critical_by_server.get(server_id, []),
            criteria_version=server_info["criteria_version"],
            last_assessed=server_info["last_assessed"],
        ))
    
    return CriteriaAuditResult(servers=servers_list)