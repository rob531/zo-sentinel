"""logic.py -- the service's data/computation layer for registry rescan backlog.

The registry is read through the application's SQLAlchemy session and the real
McpServerRegistry model. No writes or network calls are performed here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session  # noqa: F401 -- required application data layer
from app.models import McpServerRegistry


BUCKET_ORDER = (
    "never_scanned",
    "<=24h",
    "1-7d",
    "7-30d",
    ">30d",
)

_BUCKET_SET = set(BUCKET_ORDER)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_bucket(days_since_scan: Optional[int], age_hours: Optional[float]) -> str:
    if days_since_scan is None:
        return "never_scanned"
    if age_hours is not None and age_hours <= 24:
        return "<=24h"
    if days_since_scan <= 7:
        return "1-7d"
    if days_since_scan <= 30:
        return "7-30d"
    return ">30d"


def compute_rescan_backlog(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Return registry servers grouped by rescan staleness.

    ``last_scanned`` is the authoritative scan timestamp. A missing value is
    always classified as ``never_scanned``; ``last_assessed`` is read alongside
    it because it is part of the registry's assessment state, but does not
    turn an unscanned server into a scanned one.
    """
    reference = _as_utc(now) or datetime.now(timezone.utc)

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
            McpServerRegistry.last_assessed,
            McpServerRegistry.registry_source,
        )
    ).all()

    grouped: Dict[str, List[Dict[str, Any]]] = {bucket: [] for bucket in BUCKET_ORDER}

    for server_id, name, risk_tier, scan_count, last_scanned, last_assessed, _source in rows:
        scanned_at = _as_utc(last_scanned)
        if scanned_at is None:
            days_since_scan: Optional[int] = None
            age_hours: Optional[float] = None
        else:
            elapsed = reference - scanned_at
            if elapsed.total_seconds() < 0:
                elapsed = elapsed.__class__(0)
            age_hours = elapsed.total_seconds() / 3600.0
            days_since_scan = int(elapsed.total_seconds() // 86400)

        bucket = _age_bucket(days_since_scan, age_hours)
        grouped[bucket].append(
            {
                "server_id": server_id,
                "name": name,
                "risk_tier": risk_tier,
                "days_since_scan": days_since_scan,
                "scan_count": int(scan_count or 0),
            }
        )

    buckets: List[Dict[str, Any]] = []
    for bucket in BUCKET_ORDER:
        items = grouped[bucket]
        items.sort(
            key=lambda item: (
                item["days_since_scan"] is None,
                -(item["days_since_scan"] or 0),
                str(item["server_id"]),
            )
        )
        buckets.append(
            {
                "bucket": bucket,
                "count": len(items),
                "sample": items[:25],
            }
        )

    return {
        "generated_at": reference.isoformat(),
        "total_servers": len(rows),
        "buckets": buckets,
    }


def get_rescan_backlog(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Compatibility name for the primary rescan-backlog computation."""
    return compute_rescan_backlog(db, now=now)


def list_rescan_backlog(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Compatibility name for callers that use list-style service naming."""
    return compute_rescan_backlog(db, now=now)


def get_backlog(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Compatibility name for the registry backlog computation."""
    return compute_rescan_backlog(db, now=now)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_db = TestSession()

    test_now = datetime(2024, 1, 10, tzinfo=timezone.utc)
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="never",
                name="Never scanned",
                risk_tier="high",
                scan_count=0,
                last_scanned=None,
                last_assessed=None,
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="recent",
                name="Recent",
                risk_tier="low",
                scan_count=1,
                last_scanned=datetime(2024, 1, 9, tzinfo=timezone.utc),
                last_assessed=datetime(2024, 1, 9, tzinfo=timezone.utc),
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="old",
                name="Old",
                risk_tier="medium",
                scan_count=3,
                last_scanned=datetime(2023, 11, 30, tzinfo=timezone.utc),
                last_assessed=datetime(2023, 11, 30, tzinfo=timezone.utc),
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="null-scan",
                name="Null scan",
                risk_tier="low",
                scan_count=0,
                last_scanned=None,
                last_assessed=datetime(2024, 1, 1, tzinfo=timezone.utc),
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="one-day",
                name="One day",
                risk_tier="low",
                scan_count=2,
                last_scanned=datetime(2024, 1, 8, tzinfo=timezone.utc),
                last_assessed=datetime(2024, 1, 8, tzinfo=timezone.utc),
                registry_source="test",
            ),
        ]
    )
    test_db.commit()

    result = compute_rescan_backlog(test_db, now=test_now)
    counts = {entry["bucket"]: entry["count"] for entry in result["buckets"]}
    assert result["total_servers"] == 5
    assert sum(counts.values()) == 5
    assert counts["never_scanned"] == 2
    assert counts["<=24h"] == 1
    assert counts["1-7d"] == 1
    assert counts[">30d"] == 1
    never_sample = next(
        entry for entry in result["buckets"] if entry["bucket"] == "never_scanned"
    )["sample"]
    assert any(item["server_id"] == "null-scan" for item in never_sample)

    test_db.close()
    test_engine.dispose()
    print("PASS")