# services/staged/server_tier_transition_log/logic.py
from typing import Optional
from datetime import datetime, date
from collections import defaultdict

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore, McpServerRegistry


def get_server_tier_transitions(
    session: Session,
    server_id: str,
) -> dict:
    """
    Read axis scores for a server, join with registry to get risk_tier,
    track tier transitions over time, and identify which axes drove each change.
    """
    # Query axis scores joined with server registry for the given server_id
    stmt = (
        select(McpLlmAxisScore, McpServerRegistry.risk_tier)
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at)
    )
    
    rows = session.execute(stmt).all()
    
    if not rows:
        return {"server_id": server_id, "transitions": []}
    
    # Collect scores by date, tracking tier per timestamp
    daily_data = defaultdict(lambda: {"tier": None, "axes": defaultdict(float)})
    
    for score_row, risk_tier in rows:
        scored_at = score_row.scored_at
        if isinstance(scored_at, datetime):
            day_key = scored_at.date()
        else:
            day_key = scored_at
        
        daily_data[day_key]["tier"] = risk_tier
        daily_data[day_key]["axes"][score_row.axis_name] = score_row.p_danger
    
    # Sort dates and detect transitions
    sorted_dates = sorted(daily_data.keys())
    transitions = []
    prev_tier = None
    
    for day in sorted_dates:
        current_tier = daily_data[day]["tier"]
        
        if prev_tier is not None and current_tier != prev_tier:
            from_tier = prev_tier
            to_tier = current_tier
            
            # Calculate axis deltas from previous day's axes
            axis_deltas = {}
            if sorted_dates.index(day) > 0:
                prev_day = sorted_dates[sorted_dates.index(day) - 1]
                prev_axes = daily_data[prev_day]["axes"]
                curr_axes = daily_data[day]["axes"]
                
                all_axes = set(prev_axes.keys()) | set(curr_axes.keys())
                for axis in all_axes:
                    delta = curr_axes.get(axis, 0.0) - prev_axes.get(axis, 0.0)
                    if delta != 0.0:
                        axis_deltas[axis] = round(delta, 4)
            
            transitions.append({
                "date": day.isoformat() if isinstance(day, date) else str(day),
                "from_tier": from_tier,
                "to_tier": to_tier,
                "axis_deltas": axis_deltas
            })
        
        prev_tier = current_tier
    
    return {"server_id": server_id, "transitions": transitions}