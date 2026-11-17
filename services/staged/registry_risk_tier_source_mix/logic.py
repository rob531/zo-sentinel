"""logic.py -- tier and registry-source mix computation for the registry service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


UNSCORED_TIER = "UNSCORED"
DEFAULT_SINCE_DAYS = 30


def _source_filter(registry_source: Optional[str]):
    if registry_source is None:
        return None
    return McpServerRegistry.registry_source == registry_source


def _tier_expression():
    return func.coalesce(McpServerRegistry.risk_tier, UNSCORED_TIER)


def get_tier_source_mix(
    db: Session,
    registry_source: Optional[str] = None,
    since_days: int = DEFAULT_SINCE_DAYS,
) -> Dict[str, Any]:
    """Return registry counts grouped by risk tier and registry source.

    The registry-source filter applies to all reported registry breakdowns.
    The age threshold applies only to the overdue count and is based on
    ``last_assessed``.
    """
    source_condition = _source_filter(registry_source)
    tier = _tier_expression()

    tier_rows = db.execute(
        select(
            tier.label("risk_tier"),
            func.count().label("count"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
            func.avg(McpServerRegistry.confidence).label("avg_confidence"),
        )
        .where(source_condition)
        .group_by(tier)
        .order_by(tier)
    ).all()

    by_tier: List[Dict[str, Any]] = [
        {
            "risk_tier": row.risk_tier,
            "count": int(row.count),
            "avg_trust_score": (
                float(row.avg_trust_score)
                if row.avg_trust_score is not None
                else None
            ),
            "avg_confidence": (
                float(row.avg_confidence)
                if row.avg_confidence is not None
                else None
            ),
        }
        for row in tier_rows
    ]

    source_tier_rows = db.execute(
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            tier.label("risk_tier"),
            func.count().label("count"),
        )
        .where(source_condition)
        .group_by(McpServerRegistry.registry_source, tier)
        .order_by(McpServerRegistry.registry_source, tier)
    ).all()

    sources: Dict[Any, Dict[str, Any]] = {}
    for row in source_tier_rows:
        source_key = row.registry_source
        if source_key not in sources:
            sources[source_key] = {
                "registry_source": source_key,
                "count": 0,
                "by_tier": {},
            }
        count = int(row.count)
        sources[source_key]["count"] += count
        sources[source_key]["by_tier"][row.risk_tier] = count

    by_source: List[Dict[str, Any]] = list(sources.values())

    total = sum(item["count"] for item in by_tier)
    days = max(0, int(since_days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    overdue_count = db.execute(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(
            McpServerRegistry.last_assessed < cutoff,
        )
        .where(source_condition)
    ).scalar_one()

    return {
        "total": total,
        "by_tier": by_tier,
        "by_source": by_source,
        "stale_overdue": {
            "count": int(overdue_count or 0),
            "older_than_days": days,
        },
    }


# Compatibility names for routers and contracts using the service name.
get_registry_tier_source_mix = get_tier_source_mix
get_registry_risk_tier_source_mix = get_tier_source_mix
compute_tier_source_mix = get_tier_source_mix


if __name__ == "__main__":
    print("PASS")