from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_LABELS = (
    "never_scanned",
    "lt_1d",
    "lt_7d",
    "lt_30d",
    "gte_30d",
)


def _utc_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _bucket_for(last_scanned: datetime | date | None, now: datetime) -> str:
    if last_scanned is None:
        return "never_scanned"

    age_seconds = max(
        0.0,
        (now - _utc_datetime(last_scanned)).total_seconds(),
    )
    age_days = age_seconds / 86400.0

    if age_days < 1:
        return "lt_1d"
    if age_days < 7:
        return "lt_7d"
    if age_days < 30:
        return "lt_30d"
    return "gte_30d"


def get_scan_cadence(db: Session = Depends(get_session)) -> dict[str, Any]:
    rows = db.execute(
        select(
            McpServerRegistry.last_scanned,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    now = datetime.now(timezone.utc)
    bucket_counts = {label: 0 for label in BUCKET_LABELS}
    source_counts: dict[str, int] = {}
    tier_counts: dict[str, int] = {}
    never_scanned_count = 0

    for last_scanned, registry_source, risk_tier in rows:
        bucket_counts[_bucket_for(last_scanned, now)] += 1
        if last_scanned is None:
            never_scanned_count += 1

        source_key = str(registry_source) if registry_source is not None else "unknown"
        tier_key = str(risk_tier) if risk_tier is not None else "unknown"
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1

    total_servers = len(rows)

    return {
        "total_servers": total_servers,
        "scanned_count": total_servers - never_scanned_count,
        "never_scanned_count": never_scanned_count,
        "buckets": [
            {"label": label, "count": bucket_counts[label]}
            for label in BUCKET_LABELS
        ],
        "by_source": [
            {"registry_source": source, "count": count}
            for source, count in sorted(
                source_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "by_tier": [
            {"risk_tier": tier, "count": count}
            for tier, count in sorted(
                tier_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
    }


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned",
                    registry_source="test-source",
                    risk_tier="high",
                    first_seen=now - timedelta(days=50),
                    last_seen=now,
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="scanned-today",
                    name="Scanned today",
                    registry_source="test-source",
                    risk_tier="medium",
                    first_seen=now - timedelta(days=10),
                    last_seen=now,
                    last_scanned=now - timedelta(hours=2),
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="scanned-40-days-ago",
                    name="Scanned 40 days ago",
                    registry_source="other-source",
                    risk_tier="low",
                    first_seen=now - timedelta(days=100),
                    last_seen=now,
                    last_scanned=now - timedelta(days=40),
                    scan_count=1,
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()

        @test_app.get("/api/registry/scan-cadence")
        def scan_cadence_endpoint(
            payload: dict[str, Any] = Depends(get_scan_cadence),
        ) -> dict[str, Any]:
            return payload

        def override_get_session():
            with TestSession() as session:
                yield session

        test_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(test_app).get("/api/registry/scan-cadence")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        assert payload["never_scanned_count"] == 1
        gte_30d = next(
            bucket
            for bucket in payload["buckets"]
            if bucket["label"] == "gte_30d"
        )
        assert gte_30d["count"] == 1

    print("PASS")