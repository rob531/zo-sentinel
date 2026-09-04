from typing import Optional, List
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.models import McpServerRegistry


def get_tier_distribution_by_source(
    session: Session,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> List[dict]:
    query = session.query(
        McpServerRegistry.registry_source.label("source"),
        McpServerRegistry.risk_tier.label("tier"),
        func.count(McpServerRegistry.server_id).label("count"),
    )

    if start_date:
        query = query.filter(McpServerRegistry.last_seen >= start_date)
    if end_date:
        query = query.filter(McpServerRegistry.last_seen <= end_date)

    query = query.group_by(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
    )

    results = query.all()
    return [
        {"source": r.source, "tier": r.tier, "count": r.count}
        for r in results
    ]