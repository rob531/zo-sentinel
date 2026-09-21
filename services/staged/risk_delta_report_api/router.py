# services/staged/risk_delta_report_api/logic.py
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models import McpLlmAxisScore


def get_risk_delta(db: Session, server_id: str) -> dict:
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    
    if len(scores) < 2:
        return None
    
    ts_list = list(set(s.scored_at for s in scores))
    ts_list.sort(reverse=True)
    current_ts = ts_list[0]
    prev_ts = ts_list[1]
    
    current_scores = {s.axis_name: s for s in scores if s.scored_at == current_ts}
    prev_scores = {s.axis_name: s for s in scores if s.scored_at == prev_ts}
    
    axes = []
    for axis_name in sorted(current_scores.keys()):
        current = current_scores[axis_name]
        prev = prev_scores.get(axis_name)
        
        if prev:
            current_p = float(current.p_top)
            previous_p = float(prev.p_top)
        else:
            current_p = float(current.p_top)
            previous_p = 0.0
        
        delta = current_p - previous_p
        
        if delta > 0:
            direction = "increasing"
        elif delta < 0:
            direction = "decreasing"
        else:
            direction = "stable"
        
        axes.append({
            "axis_name": axis_name,
            "previous_p_top": previous_p,
            "current_p_top": current_p,
            "delta": delta,
            "direction": direction,
        })
    
    deltas = [a["delta"] for a in axes]
    overall_delta = sum(deltas) / len(deltas) if deltas else 0.0
    
    return {
        "server_id": server_id,
        "axes": axes,
        "overall_delta": overall_delta,
        "scored_at_current": current_ts,
        "scored_at_previous": prev_ts,
    }