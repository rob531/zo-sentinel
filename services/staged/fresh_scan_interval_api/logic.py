"""Logic for the fresh scan interval service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKETS = ("0-1d", "1-7d", "7-30d", "30-90d", "90d+")
_ONE_DAY = timedelta(days=1)
_SEVEN_DAYS = timedelta(days=7)
_THIRTY_DAYS = timedelta(days=30)
_NINETY_DAYS = timedelta(days=90)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_bucket(age: timedelta) -> str:
    if age < _ONE_DAY:
        return "0-1d"
    if age < _SEVEN_DAYS:
        return "1-7d"
    if age < _THIRTY_DAYS:
        return "7-30d"
    if age < _NINETY_DAYS:
        return "30-90d"
    return "90d+"


def get_fresh_scan_interval(
    db: Session, bucket_days: int = 7
) -> Dict[str, Any]:
    """Return scan-age buckets and scan-count distribution for the registry."""
    del bucket_days

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.last_scanned,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_seen,
            McpServerRegistry.scan_count,
        )
    ).all()

    now = datetime.now(timezone.utc)
    bucket_counts = {bucket: 0 for bucket in BUCKETS}
    scan_count_counts: Dict[int, int] = {}
    scanned_servers = 0
    never_scanned = 0

    for row in rows:
        scan_count = int(row.scan_count or 0)
        scan_count_counts[scan_count] = scan_count_counts.get(scan_count, 0) + 1

        if row.last_scanned is None:
            never_scanned += 1
            continue

        scanned_servers += 1
        age = now - _as_utc(row.last_scanned)
        if age.total_seconds() < 0:
            age = timedelta(0)
        bucket = _age_bucket(age)
        bucket_counts[bucket] += 1

    buckets: List[Dict[str, Any]] = [
        {"bucket": bucket, "count": bucket_counts[bucket]} for bucket in BUCKETS
    ]
    scan_count_histogram = [
        {"scan_count": scan_count, "count": scan_count_counts[scan_count]}
        for scan_count in sorted(scan_count_counts)
    ]

    return {
        "total_servers": len(rows),
        "scanned_servers": scanned_servers,
        "never_scanned": never_scanned,
        "buckets": buckets,
        "scan_count_histogram": scan_count_histogram,
    }


def compute_fresh_scan_interval(
    db: Session, bucket_days: int = 7
) -> Dict[str, Any]:
    """Compatibility wrapper for the scan-interval computation."""
    return get_fresh_scan_interval(db, bucket_days)


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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="recent",
                    last_scanned=now - timedelta(days=1),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="stale",
                    last_scanned=now - timedelta(days=40),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="frequent",
                    last_scanned=now - timedelta(hours=1),
                    scan_count=5,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/scans/fresh-interval")
    def fresh_scan_interval_endpoint(
        bucket_days: int = 7, db: Session = Depends(get_session)
    ):
        return get_fresh_scan_interval(db, bucket_days)

    response = TestClient(test_app).get(
        "/api/scans/fresh-interval", params={"bucket_days": 7}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4
    assert payload["never_scanned"] == 1
    assert next(
        item["count"] for item in payload["buckets"] if item["bucket"] == "30-90d"
    ) == 1
    assert any(
        item["scan_count"] == 5 for item in payload["scan_count_histogram"]
    )
    print("PASS")