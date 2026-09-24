# Build services/staged/axis_score_to_risk_tier_api/logic.py
from typing import Any, List, Optional
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_risk_tier(composite_score: float, missing_axes: int) -> str:
    if missing_axes >= 5:
        return "INSUFFICIENT"
    if composite_score > 75:
        return "TRUSTED_GENERAL"
    if composite_score > 60:
        return "TRUSTED_RESEARCH"
    if composite_score > 45:
        return "ENTERPRISE_CONTROLLED"
    if composite_score > 30:
        return "CAUTION_LIMITED"
    if composite_score > 15:
        return "HIGH_RISK_ISOLATED"
    return "KNOWN_THREAT"


def get_composite_score(axes: List[dict]) -> float:
    total = 0.0
    for axis in axes:
        total += axis.p_top or 0.0
    return total / 7.0 if axes else 0.0


def compute_risk_tier_scores(db: Session) -> dict:
    # Query all servers with their axis scores
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.label,
        )
        .join(
            McpLlmAxisScore,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .order_by(McpServerRegistry.server_id, McpLlmAxisScore.axis_name)
    )
    
    results = db.execute(stmt).fetchall()
    
    servers_data = {}
    for row in results:
        server_id = row.server_id
        if server_id not in servers_data:
            servers_data[server_id] = {
                "server_id": server_id,
                "name": row.name,
                "axes": {},
            }
        
        servers_data[server_id]["axes"][row.axis_name] = {
            "label": row.label,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
        }
    
    servers = []
    for server_id, data in servers_data.items():
        axes_list = list(data["axes"].values())
        composite_score = get_composite_score(axes_list)
        missing_axes = 7 - len(data["axes"])
        risk_tier = get_risk_tier(composite_score, missing_axes)
        
        servers.append({
            "server_id": server_id,
            "name": data["name"],
            "composite_score": composite_score,
            "risk_tier": risk_tier,
            "axes": data["axes"],
        })
    
    return {"servers": servers}