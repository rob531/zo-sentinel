from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = ("0-1d", "1-7d", "7-30d", "30d+")
_BUCKET_ORDER = {bucket: index for index, bucket in enumerate(BUCKETS)}


def _bucket_for_days(days_since_last_seen: int) -> str:
    if days_since_last_seen <= 1:
        return "0-1d"
    if days_since_last_seen <= 7:
        return "1-7d"
    if days_since_last_seen <= 30:
        return "7-30d"
    return "30d+"


def get_registry_last_seen_freshness(
    db: Session = Depends(get_session),
    days: int = 30,
) -> dict[str, Any]:
    """Return registry freshness buckets and the stalest recently seen servers."""
    if days < 0:
        raise ValueError("days must be non-negative")

    age_seconds = func.extract(
        "epoch", func.now() - McpServerRegistry.last_seen
    )
    days_since_last_seen = func.floor(age_seconds / 86400.0)

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.scan_count,
            days_since_last_seen.label("days_since_last_seen"),
        )
        .where(McpServerRegistry.last_seen.is_not(None))
        .order_by(
            days_since_last_seen.desc(),
            McpServerRegistry.last_seen.asc(),
            McpServerRegistry.server_id.asc(),
        )
    )

    rows = db.execute(statement).all()

    bucket_counts: Counter[str] = Counter()
    tier_bucket_counts: Counter[tuple[str, str]] = Counter()

    for row in rows:
        age = int(row.days_since_last_seen or 0)
        bucket = _bucket_for_days(age)
        bucket_counts[bucket] += 1

        risk_tier = row.risk_tier or "unknown"
        tier_bucket_counts[(risk_tier, bucket)] += 1

    buckets = [
        {"bucket": bucket, "count": int(bucket_counts.get(bucket, 0))}
        for bucket in BUCKETS
    ]

    by_tier = [
        {
            "risk_tier": risk_tier,
            "bucket": bucket,
            "count": int(count),
        }
        for (risk_tier, bucket), count in sorted(
            tier_bucket_counts.items(),
            key=lambda item: (
                _BUCKET_ORDER[item[0][1]],
                item[0][0],
            ),
        )
    ]

    stalest = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier or "unknown",
            "days_since_last_seen": int(row.days_since_last_seen or 0),
            "scan_count": int(row.scan_count or 0),
        }
        for row in rows[:10]
    ]

    return {
        "days": int(days),
        "buckets": buckets,
        "by_tier": by_tier,
        "stalest": stalest,
    }


# Compatibility aliases for staged-service callers.
get_last_seen_freshness = get_registry_last_seen_freshness
registry_last_seen_freshness = get_registry_last_seen_freshness


if __name__ == "__main__":
    assert _bucket_for_days(0) == "0-1d"
    assert _bucket_for_days(1) == "0-1d"
    assert _bucket_for_days(2) == "1-7d"
    assert _bucket_for_days(7) == "1-7d"
    assert _bucket_for_days(8) == "7-30d"
    assert _bucket_for_days(30) == "7-30d"
    assert _bucket_for_days(31) == "30d+"
    print("PASS")