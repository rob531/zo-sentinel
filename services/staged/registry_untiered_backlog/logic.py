"""logic.py -- registry untiered backlog data and computation layer.

Reads the authoritative application registry and axis-score tables through the
application SQLAlchemy session. No writes are performed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _untiered_filter():
    """Return the portable filter for registry rows without a risk tier."""
    return or_(
        McpServerRegistry.risk_tier.is_(None),
        func.trim(McpServerRegistry.risk_tier) == "",
    )


def _latest_axis_rows(db: Session, server_ids: List[str]) -> Dict[str, datetime]:
    """Return the newest axis-score timestamp for each selected server."""
    if not server_ids:
        return {}

    ranked = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.id,
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.desc().nulls_last(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(McpLlmAxisScore.server_id.in_(server_ids))
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.server_id, ranked.c.scored_at).where(
            ranked.c.row_number == 1
        )
    ).all()
    return {
        server_id: scored_at
        for server_id, scored_at in rows
        if server_id is not None and scored_at is not None
    }


def _age_days(last_assessed: Optional[datetime], now: datetime) -> int:
    if last_assessed is None:
        return 0

    if last_assessed.tzinfo is None:
        last_assessed = last_assessed.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    return max(0, (now - last_assessed).days)


def get_untiered_backlog(db: Session, days: int = 30) -> Dict[str, object]:
    """Compute the untiered registry backlog.

    ``days`` is the age threshold used for the stale/unscored detail list.
    A missing ``last_assessed`` value is treated as never assessed and is
    included in the stale list.
    """
    days = max(0, int(days))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    registry_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpServerRegistry.last_seen,
            McpServerRegistry.scan_count,
        )
        .where(_untiered_filter())
    ).all()

    server_ids = [row.server_id for row in registry_rows if row.server_id is not None]
    _latest_axis_rows(db, server_ids)

    source_rows = db.execute(
        select(McpServerRegistry.registry_source, func.count())
        .where(_untiered_filter())
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source.asc().nulls_last())
    ).all()

    by_source = [
        {
            "registry_source": source or "unknown",
            "count": int(count),
        }
        for source, count in source_rows
    ]

    stale_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_assessed,
        )
        .where(
            _untiered_filter(),
            or_(
                McpServerRegistry.last_assessed.is_(None),
                McpServerRegistry.last_assessed < cutoff,
            ),
        )
        .order_by(
            McpServerRegistry.last_assessed.asc().nulls_last(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(200)
    ).all()

    stale_unscored = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "registry_source": row.registry_source or "unknown",
            "last_assessed": row.last_assessed,
            "age_days": _age_days(row.last_assessed, now),
        }
        for row in stale_rows
    ]

    oldest_age_days = max(
        (_age_days(row.last_assessed, now) for row in registry_rows),
        default=0,
    )

    return {
        "days": days,
        "total_untiered": int(sum(item["count"] for item in by_source)),
        "by_source": by_source,
        "stale_unscored": stale_unscored,
        "oldest_age_days": int(oldest_age_days),
    }


# Compatibility aliases for callers using the common service naming variants.
compute_untiered_backlog = get_untiered_backlog
build_untiered_backlog = get_untiered_backlog
get_registry_untiered_backlog = get_untiered_backlog


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )
    TestingSession = sessionmaker(bind=engine)
    db = TestingSession()

    now = datetime.now(timezone.utc)
    db.add_all(
        [
            McpServerRegistry(
                server_id="tiered",
                name="Tiered server",
                registry_source="seed",
                risk_tier="CAUTION_LIMITED",
                last_assessed=now,
                last_seen=now,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="old-untiered",
                name="Old untiered server",
                registry_source="seed",
                risk_tier=None,
                last_assessed=now - timedelta(days=90),
                last_seen=now,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="recent-untiered",
                name="Recent untiered server",
                registry_source="seed",
                risk_tier=None,
                last_assessed=now - timedelta(days=2),
                last_seen=now,
                scan_count=1,
            ),
        ]
    )
    db.commit()

    result = get_untiered_backlog(db, days=30)
    assert result["total_untiered"] == 2
    assert sum(item["count"] for item in result["by_source"]) == 2
    assert result["stale_unscored"][0]["server_id"] == "old-untiered"

    db.close()
    print("PASS")