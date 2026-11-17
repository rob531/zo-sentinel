"""logic.py -- registry freshness lag data and computation layer.

Reads the authoritative application registry through the SQLAlchemy session and
computes per-registry-source freshness metrics without changing the schema.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    """Return a timezone-aware UTC datetime for ORM values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime consistently for the API response."""
    value = _utc(value)
    return value.isoformat() if value is not None else None


def _bucket_for_age(age_hours: Optional[float]) -> str:
    """Return the mutually exclusive age bucket for a last-seen timestamp."""
    # A missing last-seen value is treated as maximally stale so every row is
    # represented in exactly one bucket.
    if age_hours is None:
        return "gte_30d"
    if age_hours < 24:
        return "lt_24h"
    if age_hours < 24 * 7:
        return "lt_7d"
    if age_hours < 24 * 30:
        return "lt_30d"
    return "gte_30d"


def get_registry_freshness_lag(db: Session, days: int = 30) -> Dict[str, Any]:
    """Calculate freshness lag for each registry source.

    ``days`` defines the scan cutoff. A scanned server is stale when its
    ``last_scanned`` value is older than that cutoff; rows without a scan are
    reported separately as never scanned. The age buckets describe the age of
    each server's ``last_seen`` value and are mutually exclusive.
    """
    days = max(1, min(int(days), 365))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.last_seen,
            McpServerRegistry.last_scanned,
        )
    ).all()

    grouped: Dict[str, List[Tuple[Optional[datetime], Optional[datetime]]]] = {}
    for source, last_seen, last_scanned in rows:
        source_name = source or "unknown"
        grouped.setdefault(source_name, []).append((last_seen, last_scanned))

    sources: List[Dict[str, Any]] = []
    for source_name in sorted(grouped):
        source_rows = grouped[source_name]
        stale_count = 0
        never_scanned = 0
        scan_hours: List[float] = []
        oldest_last_seen: Optional[datetime] = None
        buckets = {
            "lt_24h": 0,
            "lt_7d": 0,
            "lt_30d": 0,
            "gte_30d": 0,
        }

        for last_seen, last_scanned in source_rows:
            seen_at = _utc(last_seen)
            scanned_at = _utc(last_scanned)

            if scanned_at is None:
                never_scanned += 1
            else:
                if scanned_at < cutoff:
                    stale_count += 1
                scan_hours.append(max(0.0, (now - scanned_at).total_seconds() / 3600.0))

            if seen_at is not None:
                age_hours = max(0.0, (now - seen_at).total_seconds() / 3600.0)
                buckets[_bucket_for_age(age_hours)] += 1
                if oldest_last_seen is None or seen_at < oldest_last_seen:
                    oldest_last_seen = seen_at
            else:
                buckets["gte_30d"] += 1

        avg_hours_since_scan = (
            sum(scan_hours) / len(scan_hours) if scan_hours else None
        )
        sources.append(
            {
                "registry_source": source_name,
                "total": len(source_rows),
                "stale_count": stale_count,
                "never_scanned": never_scanned,
                "avg_hours_since_scan": avg_hours_since_scan,
                "oldest_last_seen": _iso(oldest_last_seen),
                "buckets": buckets,
            }
        )

    return {
        "days": days,
        "cutoff": cutoff.isoformat(),
        "sources": sources,
        "generated_at": now.isoformat(),
    }


# Keep a concise set of names available to routers and service consumers.
get_freshness_lag = get_registry_freshness_lag
registry_freshness_lag = get_registry_freshness_lag


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    test_now = datetime.now(timezone.utc)
    seed_rows = [
        McpServerRegistry(
            server_id="freshness-test-1",
            name="Fresh one",
            registry_source="test-source",
            first_seen=test_now - timedelta(hours=2),
            last_seen=test_now - timedelta(hours=2),
            last_scanned=test_now - timedelta(hours=1),
            scan_count=4,
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="freshness-test-2",
            name="Recent",
            registry_source="test-source",
            first_seen=test_now - timedelta(days=3),
            last_seen=test_now - timedelta(days=3),
            last_scanned=test_now - timedelta(days=2),
            scan_count=3,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="freshness-test-3",
            name="Old but scanned",
            registry_source="test-source",
            first_seen=test_now - timedelta(days=60),
            last_seen=test_now - timedelta(days=40),
            last_scanned=test_now - timedelta(days=45),
            scan_count=2,
            risk_tier="high",
        ),
        McpServerRegistry(
            server_id="freshness-test-4",
            name="Never scanned",
            registry_source="test-source",
            first_seen=test_now - timedelta(days=5),
            last_seen=test_now - timedelta(hours=5),
            last_scanned=None,
            scan_count=0,
            risk_tier="unknown",
        ),
        McpServerRegistry(
            server_id="freshness-test-5",
            name="Boundary",
            registry_source="test-source",
            first_seen=test_now - timedelta(days=10),
            last_seen=test_now - timedelta(days=10),
            last_scanned=test_now - timedelta(days=1),
            scan_count=1,
            risk_tier="low",
        ),
    ]

    with TestSession() as test_db:
        test_db.add_all(seed_rows)
        test_db.commit()

        def override_get_session():
            with TestSession() as session:
                yield session

        test_app = FastAPI()

        @test_app.get("/api/registry/freshness/lag")
        def freshness_endpoint(days: int = 30, db: Session = Depends(get_session)):
            return get_registry_freshness_lag(db, days)

        test_app.dependency_overrides[get_session] = override_get_session
        response = TestClient(test_app).get(
            "/api/registry/freshness/lag", params={"days": 30}
        )
        assert response.status_code == 200
        payload = response.json()
        source = payload["sources"][0]
        assert source["total"] == 5
        assert source["never_scanned"] == 1
        assert sum(source["buckets"].values()) == 5

    print("PASS")