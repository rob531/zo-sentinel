# services/staged/axis_drift_scoring_consumer/logic.py
"""
Axis drift scoring logic for detecting score drift in MCP servers.
"""
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry

DRIFT_THRESHOLD = 0.15


def compute_axis_drift(session: Session) -> dict[str, Any]:
    """
    Read current and previous period axis scores, compute drift, flag significant changes.
    
    Returns dict with keys:
        - servers_checked: int
        - servers_flagged: int
        - axes_flagged: list of dicts
    """
    result = {
        "servers_checked": 0,
        "servers_flagged": 0,
        "axes_flagged": []
    }
    
    # Get all servers that have axis scores
    stmt = select(McpServerRegistry.server_id).distinct()
    servers = session.execute(stmt).scalars().all()
    result["servers_checked"] = len(servers)
    
    flagged_servers = set()
    
    for server_id in servers:
        drift_data = _compute_server_drift(session, server_id)
        if drift_data:
            flagged_axes = [d for d in drift_data if d["flagged"]]
            if flagged_axes:
                flagged_servers.add(server_id)
                result["axes_flagged"].extend(flagged_axes)
                _write_drift_to_registry(session, server_id, drift_data)
    
    result["servers_flagged"] = len(flagged_servers)
    return result


def _compute_server_drift(session: Session, server_id: str) -> list[dict]:
    """Compute drift for all axes of a server."""
    # Get the two most recent scored_at timestamps for this server
    time_stmt = (
        select(McpLlmAxisScore.scored_at)
        .where(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.scored_at)
        .order_by(func.max(McpLlmAxisScore.scored_at).desc())
        .limit(2)
    )
    timestamps = session.execute(time_stmt).scalars().all()
    
    if len(timestamps) < 2:
        return []
    
    current_ts, previous_ts = timestamps[0], timestamps[1]
    
    # Get scores for both periods
    current_stmt = select(McpLlmAxisScore).where(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at == current_ts
    )
    previous_stmt = select(McpLlmAxisScore).where(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at == previous_ts
    )
    
    current_scores = {s.axis_name: s for s in session.execute(current_stmt).scalars().all()}
    previous_scores = {s.axis_name: s for s in session.execute(previous_stmt).scalars().all()}
    
    drift_data = []
    all_axes = set(current_scores.keys()) | set(previous_scores.keys())
    
    for axis_name in all_axes:
        current = current_scores.get(axis_name)
        previous = previous_scores.get(axis_name)
        
        if current is None or previous is None:
            continue
        
        current_p = current.p_top or 0.0
        previous_p = previous.p_top or 0.0
        delta = abs(current_p - previous_p)
        
        drift_entry = {
            "axis_name": axis_name,
            "current_p_top": current_p,
            "previous_p_top": previous_p,
            "delta": delta,
            "flagged": delta > DRIFT_THRESHOLD
        }
        drift_data.append(drift_entry)
    
    return drift_data


def _write_drift_to_registry(session: Session, server_id: str, drift_data: list[dict]) -> None:
    """Write drift metadata back to McpServerRegistry.meta as JSON blob."""
    stmt = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    server = session.execute(stmt).scalars().first()
    
    if server:
        meta = server.meta or {}
        for entry in drift_data:
            axis_name = entry["axis_name"]
            meta[axis_name] = {
                "current_p_top": entry["current_p_top"],
                "previous_p_top": entry["previous_p_top"],
                "delta": entry["delta"],
                "flagged": entry["flagged"]
            }
        server.meta = meta
        session.commit()