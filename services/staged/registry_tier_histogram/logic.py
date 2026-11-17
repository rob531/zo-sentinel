"""logic.py – data/computation layer for the registry_tier_histogram service.

Provides a pure function that builds a risk‑tier histogram directly from the
real `mcp_server_registry` table using the application's SQLAlchemy session.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def build_tier_histogram(db: Session) -> Dict[str, Any]:
    """
    Compute a histogram of risk tiers for all MCP servers.

    Returns a dictionary with the shape:
    {
        "total": int,
        "unscored": int,
        "tiers": [
            {
                "tier": str | None,
                "count": int,
                "avg_trust": float | None,
                "min_trust": float | None,
                "max_trust": float | None,
            },
            ...
        ],
        "computed_at": iso8601‑timestamp,
    }

    The ``tiers`` list contains one entry per distinct ``risk_tier`` value
    present in the table, plus an entry where ``tier`` is ``None`` for rows
    with a null ``risk_tier``.
    """
    # total number of rows
    total = db.execute(
        select(func.count()).select_from(McpServerRegistry)
    ).scalar_one()

    # rows where risk_tier is NULL (unscored)
    unscored = db.execute(
        select(func.count()).where(McpServerRegistry.risk_tier.is_(None))
    ).scalar_one()

    # aggregates for each non‑null risk_tier
    tier_rows = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(),
            func.avg(McpServerRegistry.trust_score),
            func.min(McpServerRegistry.trust_score),
            func.max(McpServerRegistry.trust_score),
        )
        .where(McpServerRegistry.risk_tier.is_not(None))
        .group_by(McpServerRegistry.risk_tier)
    ).all()

    tiers: List[Dict[str, Any]] = []
    for tier, count, avg, mn, mx in tier_rows:
        tiers.append(
            {
                "tier": tier,
                "count": int(count),
                "avg_trust": float(avg) if avg is not None else None,
                "min_trust": float(mn) if mn is not None else None,
                "max_trust": float(mx) if mx is not None else None,
            }
        )

    # aggregates for rows with NULL risk_tier
    null_agg = db.execute(
        select(
            func.count(),
            func.avg(McpServerRegistry.trust_score),
            func.min(McpServerRegistry.trust_score),
            func.max(McpServerRegistry.trust_score),
        ).where(McpServerRegistry.risk_tier.is_(None))
    ).one()
    null_count, null_avg, null_min, null_max = null_agg
    if null_count:
        tiers.append(
            {
                "tier": None,
                "count": int(null_count),
                "avg_trust": float(null_avg) if null_avg is not None else None,
                "min_trust": float(null_min) if null_min is not None else None,
                "max_trust": float(null_max) if null_max is not None else None,
            }
        )

    result: Dict[str, Any] = {
        "total": int(total),
        "unscored": int(unscored),
        "tiers": tiers,
        "computed_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
    }
    return result