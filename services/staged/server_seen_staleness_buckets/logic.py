"""logic.py -- the service's data/computation layer (one concern, one file).

Mirrors the exemplar doctrine: import the REAL data layer (app.db/app.models),
never an inline/stub/SQLite-dict model. Pure functions over a Session so the
router stays thin and the contract can exercise this directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_ORDER = ("fresh", "recent", "stale", "dormant", "lost", "never_seen")
SESSION_DEPENDENCY = get_session


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Return a timestamp in UTC, treating naive timestamps as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(
    reference: datetime,
    last_seen: Optional[datetime],
    first_seen: Optional[datetime],
) -> Optional[int]:
    """Calculate the age of a registry server in whole days."""
    seen = last_seen if last_seen is not None else first_seen
    seen = _as_utc(seen)
    if seen is None:
        return None

    reference = _as_utc(reference)
    assert reference is not None
    return int((reference - seen).total_seconds() // 86400)


def _bucket_for_age(age_days: Optional[int]) -> str:
    """Map an age in days to its dashboard bucket."""
    if age_days is None:
        return "never_seen"
    if age_days <= 7:
        return "fresh"
    if age_days <= 30:
        return "recent"
    if age_days <= 90:
        return "stale"
    if age_days <= 365:
        return "dormant"
    return "lost"


def get_seen_staleness_buckets(
    db: Session,
    days: int = 30,
    reference_time: Optional[datetime] = None,
) -> Dict[str, object]:
    """Build the registry seen-staleness response from the real registry table."""
    if days < 0:
        raise ValueError("days must be non-negative")

    now = _as_utc(reference_time or datetime.now(timezone.utc))
    assert now is not None

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
            McpServerRegistry.first_seen,
            McpServerRegistry.scan_count,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
        )
    ).all()

    bucket_counts: Dict[str, int] = {bucket: 0 for bucket in BUCKET_ORDER}
    bucket_tiers: Dict[str, Dict[str, int]] = {
        bucket: {} for bucket in BUCKET_ORDER
    }
    source_counts: Dict[str, int] = {}
    source_never_seen: Dict[str, int] = {}
    never_seen_count = 0
    last_scanned_missing_count = 0

    for row in rows:
        registry_source = row.registry_source or "unknown"
        source_counts[registry_source] = source_counts.get(registry_source, 0) + 1

        if row.last_scanned is None:
            last_scanned_missing_count += 1

        age_days = _age_days(now, row.last_seen, row.first_seen)
        bucket = _bucket_for_age(age_days)
        bucket_counts[bucket] += 1

        if bucket == "never_seen":
            never_seen_count += 1
            source_never_seen[registry_source] = (
                source_never_seen.get(registry_source, 0) + 1
            )

        tier = row.risk_tier or "unknown"
        tiers = bucket_tiers[bucket]
        tiers[tier] = tiers.get(tier, 0) + 1

    buckets = []
    for bucket in BUCKET_ORDER:
        buckets.append(
            {
                "bucket": bucket,
                "count": bucket_counts[bucket],
                "tiers": bucket_tiers[bucket],
            }
        )

    by_source = []
    for registry_source in sorted(source_counts):
        by_source.append(
            {
                "registry_source": registry_source,
                "count": source_counts[registry_source],
                "never_seen": source_never_seen.get(registry_source, 0),
            }
        )

    return {
        "days": days,
        "as_of": now.isoformat(),
        "total_servers": len(rows),
        "buckets": buckets,
        "by_source": by_source,
        "never_seen_count": never_seen_count,
        "last_scanned_missing_count": last_scanned_missing_count,
    }


# Compatibility aliases for callers that use the service name directly.
get_staleness_buckets = get_seen_staleness_buckets
get_registry_seen_staleness = get_seen_staleness_buckets


if __name__ == "__main__":
    reference = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert _bucket_for_age(0) == "fresh"
    assert _bucket_for_age(7) == "fresh"
    assert _bucket_for_age(8) == "recent"
    assert _bucket_for_age(30) == "recent"
    assert _bucket_for_age(31) == "stale"
    assert _bucket_for_age(90) == "stale"
    assert _bucket_for_age(91) == "dormant"
    assert _bucket_for_age(365) == "dormant"
    assert _bucket_for_age(366) == "lost"
    assert _bucket_for_age(None) == "never_seen"
    assert _age_days(
        reference,
        reference - timedelta(days=31),
        None,
    ) == 31
    print("PASS")