# services/staged/server_scoring_history_api/logic.py
from datetime import date
from typing import List, Dict, Any
from sqlalchemy import func, cast, Date
from sqlalchemy.orm import Session

from app.models import McpLlmAxisScore


def get_scoring_history(db: Session, server_id: str) -> List[Dict[str, Any]]:
    """Get scoring history for a server grouped by date."""
    results = (
        db.query(
            cast(McpLlmAxisScore.scored_at, Date).label("scored_date"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.count(McpLlmAxisScore.id).label("axis_count"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(cast(McpLlmAxisScore.scored_at, Date))
        .order_by(cast(McpLlmAxisScore.scored_at, Date).asc())
        .all()
    )
    
    return [
        {
            "date": row.scored_date,
            "avg_p_top": row.avg_p_top,
            "avg_p_critical": row.avg_p_critical,
            "axis_count": row.axis_count,
        }
        for row in results
    ]