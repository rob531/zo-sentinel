from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


def _as_utc(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_in_days(value: datetime | date | None, now: datetime) -> int | None:
    timestamp = _as_utc(value)
    if timestamp is None:
        return None
    return max(0, (now - timestamp).days)


def _bucket_edges(oldest_days: int | None, buckets: int) -> list[int]:
    if oldest_days is None:
        return []

    capacity = max(1, oldest_days + 1)
    return [
        min(capacity, ceil(capacity * index / buckets))
        for index in range(1, buckets + 1)
    ]


def _scan_staleness(
    session: Session,
    buckets: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if buckets < 1:
        raise ValueError("buckets must be at least 1")

    current_time = now or datetime.now(timezone.utc)
    current_time = _as_utc(current_time) or current_time

    total = int(
        session.execute(
            select(func.count()).select_from(McpServerRegistry)
        ).scalar_one()
    )
    never_scanned = int(
        session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_scanned.is_(None))
        ).scalar_one()
    )

    grouped_rows = session.execute(
        select(
            McpServerRegistry.last_scanned,
            func.count().label("server_count"),
        )
        .group_by(McpServerRegistry.last_scanned)
        .order_by(McpServerRegistry.last_scanned)
    ).all()

    sample_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.last_scanned,
        )
        .order_by(
            McpServerRegistry.last_scanned,
            McpServerRegistry.server_id,
        )
    ).all()

    ages = [
        age
        for age in (
            _age_in_days(last_scanned, current_time)
            for last_scanned, _count in grouped_rows
        )
        if age is not None
    ]
    oldest_days = max(ages) if ages else None
    bucket_edges_days = _bucket_edges(oldest_days, buckets)

    bucket_counts = [0] * buckets
    bucket_samples: list[list[str]] = [[] for _ in range(buckets)]

    def bucket_index(age: int) -> int:
        if not bucket_edges_days:
            return 0
        return min(buckets - 1, bisect_left(bucket_edges_days, age))

    for last_scanned, server_count in grouped_rows:
        age = _age_in_days(last_scanned, current_time)
        if age is None:
            continue
        bucket_counts[bucket_index(age)] += int(server_count)

    for server_id, last_scanned in sample_rows:
        age = _age_in_days(last_scanned, current_time)
        if age is None:
            continue
        index = bucket_index(age)
        if len(bucket_samples[index]) < 5:
            bucket_samples[index].append(str(server_id))

    result_buckets: list[dict[str, Any]] = []
    lower_edge = 0
    for index, upper_edge in enumerate(bucket_edges_days):
        result_buckets.append(
            {
                "label": f"{lower_edge}-{upper_edge} days",
                "count": bucket_counts[index],
                "server_ids_sample": bucket_samples[index],
            }
        )
        lower_edge = upper_edge

    return {
        "bucket_edges_days": bucket_edges_days,
        "buckets": result_buckets,
        "never_scanned": never_scanned,
        "total": total,
        "oldest_days": oldest_days,
    }


@router.get("/registry/scan-staleness")
def scan_staleness(
    buckets: int = Query(default=5, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _scan_staleness(session=session, buckets=buckets)


app = FastAPI(title="MCP Scan Staleness API")
app.include_router(router)


def _run_contract() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    McpServerRegistry.__table__.create(bind=engine)
    session = TestingSession()
    now = datetime.now(timezone.utc)

    session.add_all(
        [
            McpServerRegistry(
                server_id="contract-scanned-now",
                name="Scanned now",
                registry_source="contract",
                last_scanned=now,
                last_seen=now,
                first_seen=now - timedelta(days=100),
                scan_count=10,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="contract-scanned-10-days",
                name="Scanned ten days ago",
                registry_source="contract",
                last_scanned=now - timedelta(days=10),
                last_seen=now,
                first_seen=now - timedelta(days=100),
                scan_count=5,
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="contract-scanned-40-days",
                name="Scanned forty days ago",
                registry_source="contract",
                last_scanned=now - timedelta(days=40),
                last_seen=now - timedelta(days=40),
                first_seen=now - timedelta(days=100),
                scan_count=1,
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="contract-never-scanned",
                name="Never scanned",
                registry_source="contract",
                last_scanned=None,
                last_seen=now,
                first_seen=now - timedelta(days=100),
                scan_count=0,
                risk_tier="low",
            ),
        ]
    )
    session.commit()

    def override_get_session() -> Session:
        return session

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/registry/scan-staleness",
                params={"buckets": 5},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4, payload
        assert payload["never_scanned"] == 1, payload
        assert (
            sum(bucket["count"] for bucket in payload["buckets"])
            + payload["never_scanned"]
            == payload["total"]
        ), payload
        assert payload["oldest_days"] is not None, payload
        assert payload["oldest_days"] >= 40, payload
        assert len(payload["bucket_edges_days"]) == 5, payload
        assert len(payload["buckets"]) == 5, payload
    finally:
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_contract()