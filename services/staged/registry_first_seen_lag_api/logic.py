from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Iterator

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

BUCKETS = ("0-1d", "1-7d", "7-30d", "30-90d", "90d+")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_registry_first_seen_lag(
    session: Session, days: int = 30
) -> Dict[str, Any]:
    if days <= 0:
        raise ValueError("days must be greater than zero")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    rows = session.execute(
        select(McpServerRegistry.registry_source, McpServerRegistry.first_seen)
    ).all()

    grouped: Dict[str, list[float]] = {}
    for registry_source, first_seen in rows:
        if first_seen is None:
            continue

        first_seen_utc = _as_utc(first_seen)
        if first_seen_utc < cutoff or first_seen_utc > now:
            continue

        age_days = max(0.0, (now - first_seen_utc).total_seconds() / 86400.0)
        source = registry_source if registry_source is not None else "unknown"
        grouped.setdefault(source, []).append(age_days)

    sources = []
    for registry_source in sorted(grouped):
        ages = grouped[registry_source]
        counts = {bucket: 0 for bucket in BUCKETS}

        for age in ages:
            if age < 1:
                counts["0-1d"] += 1
            elif age < 7:
                counts["1-7d"] += 1
            elif age < 30:
                counts["7-30d"] += 1
            elif age < 90:
                counts["30-90d"] += 1
            else:
                counts["90d+"] += 1

        sources.append(
            {
                "registry_source": registry_source,
                "total": len(ages),
                "buckets": [
                    {"bucket": bucket, "count": counts[bucket]}
                    for bucket in BUCKETS
                ],
                "median_age_days": float(median(ages)),
            }
        )

    return {
        "days": days,
        "sources": sources,
        "total_servers": sum(len(ages) for ages in grouped.values()),
    }


def get_first_seen_lag(session: Session, days: int = 30) -> Dict[str, Any]:
    return get_registry_first_seen_lag(session, days)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc)
    seeded_counts = {"registry-alpha": 3, "registry-beta": 2}
    seeded_rows = [
        ("registry-alpha", "alpha-1", now - timedelta(hours=6)),
        ("registry-alpha", "alpha-2", now - timedelta(days=2)),
        ("registry-alpha", "alpha-3", now - timedelta(days=10)),
        ("registry-beta", "beta-1", now - timedelta(days=45)),
        ("registry-beta", "beta-2", now - timedelta(days=100)),
    ]

    with testing_session() as session:
        for registry_source, server_id, first_seen in seeded_rows:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    registry_source=registry_source,
                    first_seen=first_seen,
                    last_seen=first_seen,
                    risk_tier="unknown",
                )
            )
        session.commit()

    def override_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/registry/first-seen-lag")
    def first_seen_lag(
        days: int = 30, session: Session = Depends(get_session)
    ) -> Dict[str, Any]:
        return get_registry_first_seen_lag(session, days)

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/first-seen-lag", params={"days": 120}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 120
        assert len(payload["sources"]) == 2
        assert payload["total_servers"] == sum(seeded_counts.values())

        by_source = {item["registry_source"]: item for item in payload["sources"]}
        for source, expected_count in seeded_counts.items():
            item = by_source[source]
            assert item["total"] == expected_count
            assert sum(bucket["count"] for bucket in item["buckets"]) == expected_count
            assert item["median_age_days"] >= 0

    print("PASS")


if __name__ == "__main__":
    _self_test()