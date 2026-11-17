from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_ORDER = (
    "0-1d",
    "1-7d",
    "7-30d",
    "30-90d",
    "90d-plus",
    "never-assessed",
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_verdict_age_buckets(
    session: Session = Depends(get_session),
    bucket_days: int = 30,
) -> dict[str, Any]:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_assessed,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        McpServerRegistry.registry_source,
    )
    rows = session.execute(statement).all()

    now = datetime.now(timezone.utc)
    buckets: dict[str, dict[str, Any]] = {
        name: {"bucket": name, "count": 0, "by_tier": {}}
        for name in BUCKET_ORDER
    }

    never_assessed = 0
    stale_over_30d = 0

    for row in rows:
        tier = row.risk_tier if row.risk_tier is not None else row.verdict
        tier = str(tier) if tier is not None else "unknown"

        assessed_at = _as_utc(row.last_assessed)
        if assessed_at is None:
            bucket = "never-assessed"
            never_assessed += 1
        else:
            age = now - assessed_at
            age_seconds = age.total_seconds()

            if age_seconds < 1 * 86400:
                bucket = "0-1d"
            elif age_seconds < 7 * 86400:
                bucket = "1-7d"
            elif age_seconds < 30 * 86400:
                bucket = "7-30d"
            elif age_seconds < 90 * 86400:
                bucket = "30-90d"
            else:
                bucket = "90d-plus"

            if age_seconds > 30 * 86400:
                stale_over_30d += 1

        entry = buckets[bucket]
        entry["count"] += 1
        entry["by_tier"][tier] = entry["by_tier"].get(tier, 0) + 1

    return {
        "total_servers": len(rows),
        "buckets": [buckets[name] for name in BUCKET_ORDER],
        "never_assessed": never_assessed,
        "stale_over_30d": stale_over_30d,
    }


get_registry_verdict_age_buckets = get_verdict_age_buckets


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    current_time = datetime.now(timezone.utc)
    session.add_all(
        [
            McpServerRegistry(
                server_id="age-now",
                name="now",
                verdict="trusted",
                risk_tier="low",
                last_assessed=current_time,
                first_seen=current_time,
                last_seen=current_time,
                last_scanned=current_time,
                scan_count=1,
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="age-five-days",
                name="five-days",
                verdict="review",
                risk_tier=None,
                last_assessed=current_time - timedelta(days=5),
                first_seen=current_time - timedelta(days=10),
                last_seen=current_time,
                last_scanned=current_time,
                scan_count=1,
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="age-sixty-days",
                name="sixty-days",
                verdict="malicious",
                risk_tier="critical",
                last_assessed=current_time - timedelta(days=60),
                first_seen=current_time - timedelta(days=90),
                last_seen=current_time - timedelta(days=60),
                last_scanned=current_time - timedelta(days=60),
                scan_count=1,
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="age-never",
                name="never",
                verdict="unknown",
                risk_tier=None,
                last_assessed=None,
                first_seen=current_time,
                last_seen=current_time,
                last_scanned=None,
                scan_count=0,
                registry_source="test",
            ),
        ]
    )
    session.commit()

    def override_get_session():
        yield session

    test_app = FastAPI()

    @test_app.get("/api/registry/verdict-age-buckets")
    def registry_verdict_age_buckets(
        bucket_days: int = 30,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_verdict_age_buckets(session=db, bucket_days=bucket_days)

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/verdict-age-buckets",
        params={"bucket_days": 30},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    bucket_counts = {item["bucket"]: item["count"] for item in payload["buckets"]}
    assert payload["total_servers"] == 4
    assert payload["never_assessed"] == 1
    assert bucket_counts["0-1d"] == 1
    assert bucket_counts["30-90d"] == 1
    assert payload["stale_over_30d"] == 1
    print("PASS")