"""logic.py -- scan freshness computation over the real server registry."""

from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


BUCKETS = ("0-1d", "2-7d", "8-30d", "31-90d", "90d+", "never")


def _utc_datetime(value: Any) -> datetime:
    """Normalize database timestamp values to UTC-aware datetimes."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    raise TypeError(f"Unsupported timestamp value: {value!r}")


def _age_days(last_scanned: Any, now: datetime) -> int:
    elapsed = now - _utc_datetime(last_scanned)
    return max(0, int(elapsed.total_seconds() // 86400))


def _bucket_for_age(age_days: Optional[int]) -> str:
    if age_days is None:
        return "never"
    if age_days <= 1:
        return "0-1d"
    if age_days <= 7:
        return "2-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "90d+"


def get_scan_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return scan-freshness buckets and totals for the registry.

    ``days`` is the reporting window metadata. The fixed freshness buckets are
    always evaluated so that servers older than the window remain visible in
    the stale buckets.
    """
    if days <= 0:
        raise ValueError("days must be greater than zero")

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_scanned,
        )
    ).all()

    entries: List[Dict[str, Any]] = []
    for server_id, name, registry_source, risk_tier, last_scanned in rows:
        age_days = None if last_scanned is None else _age_days(last_scanned, now)
        entries.append(
            {
                "server_id": server_id,
                "name": name,
                "registry_source": registry_source,
                "risk_tier": risk_tier,
                "age_days": age_days,
                "_bucket": _bucket_for_age(age_days),
            }
        )

    buckets: List[Dict[str, Any]] = []
    for bucket in BUCKETS:
        bucket_entries = [entry for entry in entries if entry["_bucket"] == bucket]
        if bucket == "never":
            bucket_entries.sort(key=lambda entry: str(entry["server_id"]))
        else:
            bucket_entries.sort(
                key=lambda entry: (-int(entry["age_days"]), str(entry["server_id"]))
            )

        servers = [
            {
                "server_id": entry["server_id"],
                "name": entry["name"],
                "registry_source": entry["registry_source"],
                "risk_tier": entry["risk_tier"],
                "age_days": entry["age_days"],
            }
            for entry in bucket_entries[:20]
        ]
        buckets.append({"bucket": bucket, "count": len(bucket_entries), "servers": servers})

    ages = [entry["age_days"] for entry in entries if entry["age_days"] is not None]
    totals = {
        "total_servers": len(entries),
        "never_scanned": sum(1 for entry in entries if entry["age_days"] is None),
        "stale_over_30d": sum(1 for age in ages if age > 30),
        "median_age_days": median(ages) if ages else None,
    }

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "buckets": buckets,
        "totals": totals,
    }


def scan_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the service operation name."""
    return get_scan_freshness(db, days)


def compute_scan_freshness(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the computation name."""
    return get_scan_freshness(db, days)


if __name__ == "__main__":
    assert _bucket_for_age(1) == "0-1d"
    assert _bucket_for_age(2) == "2-7d"
    assert _bucket_for_age(8) == "8-30d"
    assert _bucket_for_age(31) == "31-90d"
    assert _bucket_for_age(91) == "90d+"
    assert _bucket_for_age(None) == "never"
    assert median([1, 10, 40]) == 10
    print("PASS")