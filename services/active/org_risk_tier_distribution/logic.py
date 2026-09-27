# deps: fastapi, pydantic, sqlalchemy
"""logic.py -- data/computation layer for org_risk_tier_distribution."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def org_risk_tier_distribution(db: Session) -> list[tuple[str, str, int]]:
    """Return [(org_id, risk_tier, count), ...] for all servers."""
    rows = (
        db.execute(
            select(
                McpServerRegistry.org_id,
                McpServerRegistry.risk_tier,
                func.count(McpServerRegistry.server_id).label("cnt"),
            ).group_by(
                McpServerRegistry.org_id,
                McpServerRegistry.risk_tier,
            ).order_by(
                McpServerRegistry.org_id,
                McpServerRegistry.risk_tier,
            )
        )
        .all()
    )
    return [(r[0] or "unknown", r[1] or "unknown", int(r[2])) for r in rows]
