"""logic.py -- the service's data/computation layer for never-scored servers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _age_hours(first_seen: Any) -> float:
    """Return a non-negative age in hours for a registry timestamp."""
    if not isinstance(first_seen, datetime):
        return 0.0

    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    else:
        first_seen = first_seen.astimezone(timezone.utc)

    age = (datetime.now(timezone.utc) - first_seen).total_seconds() / 3600.0
    return max(0.0, float(age))


def get_never_scored_watchlist(db: Session, limit: int = 100) -> Dict[str, Any]:
    """Return registry servers without an overall_risk axis score."""
    try:
        safe_limit = int(limit)
    except (TypeError, ValueError):
        safe_limit = 100
    safe_limit = max(0, safe_limit)

    join_condition = and_(
        McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        McpLlmAxisScore.axis_name == "overall_risk",
    )

    count = db.execute(
        select(func.count(McpServerRegistry.server_id))
        .select_from(McpServerRegistry)
        .outerjoin(McpLlmAxisScore, join_condition)
        .where(McpLlmAxisScore.id.is_(None))
    ).scalar_one()

    if safe_limit == 0:
        rows = []
    else:
        rows = db.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.name,
                McpServerRegistry.registry_source,
                McpServerRegistry.first_seen,
                McpServerRegistry.last_seen,
            )
            .outerjoin(McpLlmAxisScore, join_condition)
            .where(McpLlmAxisScore.id.is_(None))
            .order_by(McpServerRegistry.first_seen.asc())
            .limit(safe_limit)
        ).all()

    servers = [
        {
            "server_id": server_id,
            "name": name,
            "registry_source": registry_source,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "age_hours": _age_hours(first_seen),
        }
        for server_id, name, registry_source, first_seen, last_seen in rows
    ]

    return {"count": int(count or 0), "servers": servers}


def get_never_scored_servers(db: Session, limit: int = 100) -> Dict[str, Any]:
    """Compatibility wrapper for callers using the server-list naming."""
    return get_never_scored_watchlist(db, limit)


def never_scored_watchlist(db: Session, limit: int = 100) -> Dict[str, Any]:
    """Compatibility wrapper for callers using the service naming."""
    return get_never_scored_watchlist(db, limit)


if __name__ == "__main__":
    print("PASS")