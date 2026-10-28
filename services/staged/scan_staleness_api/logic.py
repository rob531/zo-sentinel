from __future__ import annotations

from bisect import bisect_right
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])


@contextmanager
def _session_scope() -> Iterator[Session]:
    provider = get_session()
    if hasattr(provider, "__enter__"):
        with provider as session:
            yield session
        return

    iterator = iter(provider)
    session = next(iterator)
    try:
        yield session
    finally:
        try:
            next(iterator)
        except StopIteration:
            pass
        finally:
            session.close()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: datetime, now: datetime) -> int:
    elapsed = now - _as_utc(value)
    return max(0, elapsed.days)


def _bucket_edges(oldest_days: int | None, buckets: int) -> list[int]:
    span = max((oldest_days or 0) + 1, buckets)
    return [((span * index) + buckets - 1) // buckets for index in range(1, buckets + 1)]


def _scan_staleness(
    session: Session,
    buckets: int = 5,
    now: datetime | None = None,
) -> dict:
    if buckets < 1:
        raise ValueError("buckets must be at least 1")

    now = now or _utc_now()

    total = (
        session.query(func.count())
        .select_from(McpServerRegistry)
        .scalar()
        or 0
    )
    never_scanned = (
        session.query(func.count())
        .select_from(McpServerRegistry)
        .filter(McpServerRegistry.last_scanned.is_(None))
        .scalar()
        or 0
    )

    grouped_rows = (
        session.query(
            McpServerRegistry.last_scanned.label("last_scanned"),
            func.count().label("server_count"),
        )
        .group_by(McpServerRegistry.last_scanned)
        .all()
    )

    ages_by_timestamp: dict[datetime | None, list[int]] = {}
    oldest_days: int | None = None
    for row in grouped_rows:
        if row.last_scanned is None:
            continue
        age = _age_days(row.last_scanned, now)
        ages_by_timestamp.setdefault(row.last_scanned, []).append(age)
        oldest_days = age if oldest_days is None else max(oldest_days, age)

    edges = _bucket_edges(oldest_days, buckets)
    counts = [0] * buckets
    for row in grouped_rows:
        if row.last_scanned is None:
            continue
        age = _age_days(row.last_scanned, now)
        bucket_index = bisect_right(edges, age)
        if bucket_index < buckets:
            counts[bucket_index] += int(row.server_count)

    server_rows = (
        session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.last_scanned,
        )
        .order_by(McpServerRegistry.server_id)
        .all()
    )
    samples: list[list[str]] = [[] for _ in range(buckets)]
    for row in server_rows:
        if row.last_scanned is None:
            continue
        age = _age_days(row.last_scanned, now)
        bucket_index = bisect_right(edges, age)
        if bucket_index < buckets and len(samples[bucket_index]) < 5:
            samples[bucket_index].append(str(row.server_id))

    bucket_output = []
    for index, upper in enumerate(edges):
        lower = 0 if index == 0 else edges[index - 1]
        bucket_output.append(
            {
                "label": f"{lower}-{upper - 1} days",
                "count": counts[index],
                "server_ids_sample": samples[index],
            }
        )

    return {
        "bucket_edges_days": edges,
        "buckets": bucket_output,
        "never_scanned": int(never_scanned),
        "total": int(total),
        "oldest_days": oldest_days,
    }


def scan_staleness(buckets: int = 5) -> dict:
    with _session_scope() as session:
        return _scan_staleness(session, buckets)


@router.get("/registry/scan-staleness")
def get_scan_staleness(
    buckets: int = Query(5, ge=1),
    session: Session = Depends(get_session),
) -> dict:
    return _scan_staleness(session, buckets)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="scanned-now",
                name="Scanned now",
                registry_source="self-test",
                first_seen=now,
                last_seen=now,
                last_scanned=now,
                scan_count=1,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="scanned-10-days",
                name="Scanned 10 days ago",
                registry_source="self-test",
                first_seen=now,
                last_seen=now,
                last_scanned=now.replace() - __import__("datetime").timedelta(days=10),
                scan_count=1,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="scanned-40-days",
                name="Scanned 40 days ago",
                registry_source="self-test",
                first_seen=now,
                last_seen=now,
                last_scanned=now.replace() - __import__("datetime").timedelta(days=40),
                scan_count=1,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="never-scanned",
                name="Never scanned",
                registry_source="self-test",
                first_seen=now,
                last_seen=now,
                last_scanned=None,
                scan_count=0,
                risk_tier="low",
            ),
        ]
    )
    session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/registry/scan-staleness", params={"buckets": 5})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert payload["never_scanned"] == 1
        assert sum(bucket["count"] for bucket in payload["buckets"]) + payload[
            "never_scanned"
        ] == payload["total"]
        assert payload["oldest_days"] >= 40
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()