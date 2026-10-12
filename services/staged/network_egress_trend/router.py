# services/staged/network_egress_trend/logic.py
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import McpLlmAxisScore, McpServerRegistry


def get_network_egress_trend(session: Session, days: int) -> list[dict]:
    """
    Returns network egress trend data aggregated by date and label bucket.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    results = (
        session.query(
            func.date(McpLlmAxisScore.scored_at).label("date"),
            McpLlmAxisScore.label,
            func.count(McpLlmAxisScore.id).label("count"),
            func.avg(McpLlmAxisScore.p_top).label("p_top_avg"),
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .filter(McpLlmAxisScore.axis_name == "network_egress")
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(
            func.date(McpLlmAxisScore.scored_at),
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        )
        .order_by(
            func.date(McpLlmAxisScore.scored_at),
            McpLlmAxisScore.label_index,
        )
        .all()
    )

    return [
        {
            "date": str(row.date),
            "label": row.label,
            "count": row.count,
            "p_top_avg": float(row.p_top_avg) if row.p_top_avg is not None else None,
        }
        for row in results
    ]