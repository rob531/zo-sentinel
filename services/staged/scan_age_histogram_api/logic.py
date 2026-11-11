from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_LABELS = (
    "0-1h",
    "1-24h",
    "1-7d",
    "7-30d",
    "over-30d",
    "never-scanned",
)
MAX_SERVERS_PER_BUCKET = 100


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for(last_scanned: datetime | None, now: datetime) -> str:
    if last_scanned is None:
        return "never-scanned"

    age = now - _as_utc(last_scanned)
    if age <= timedelta(hours=1):
        return "0-1h"
    if age <= timedelta(hours=24):
        return "1-24h"
    if age <= timedelta(days=7):
        return "1-7d"
    if age <= timedelta(days=30):
        return "7-30d"
    return "over-30d"


def get_scan_age_histogram(session: Session = Depends(get_session)) -> dict[str, Any]:
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.last_scanned,
        McpServerRegistry.scan_count,
        McpServerRegistry.registry_source,
    )
    rows = session.execute(statement).all()

    now = datetime.now(timezone.utc)
    bucket_servers: dict[str, list[Any]] = {label: [] for label in BUCKET_LABELS}
    source_counts: Counter[Any] = Counter()
    zero_scan_count = 0
    never_scanned = 0

    for row in rows:
        label = _bucket_for(row.last_scanned, now)
        bucket_servers[label].append(row.server_id)
        source_counts[row.registry_source] += 1

        if row.last_scanned is None:
            never_scanned += 1
        if row.scan_count == 0:
            zero_scan_count += 1

    buckets = []
    for label in BUCKET_LABELS:
        servers = bucket_servers[label]
        servers.sort(key=lambda value: (value is not None, str(value)))
        buckets.append(
            {
                "label": label,
                "count": len(servers),
                "servers": servers[:MAX_SERVERS_PER_BUCKET],
            }
        )

    by_source = [
        {"registry_source": source, "count": count}
        for source, count in sorted(
            source_counts.items(),
            key=lambda item: (
                -item[1],
                item[0] is not None,
                str(item[0]).casefold() if item[0] is not None else "",
            ),
        )
    ]

    return {
        "total_servers": len(rows),
        "buckets": buckets,
        "by_source": by_source,
        "never_scanned": never_scanned,
        "zero_scan_count": zero_scan_count,
    }


scan_age_histogram = get_scan_age_histogram


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    seeded_at = datetime.now(timezone.utc)
    test_servers = [
        ("never-scanned", None, 0),
        ("ten-minutes", seeded_at - timedelta(minutes=10), 1),
        ("one-hour", seeded_at - timedelta(hours=1), 2),
        ("three-days", seeded_at - timedelta(days=3), 3),
        ("thirty-days", seeded_at - timedelta(days=30), 4),
        ("sixty-days", seeded_at - timedelta(days=60), 5),
    ]

    with testing_session() as session:
        for server_id, last_scanned, scan_count in test_servers:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=server_id,
                    url=f"https://{server_id}.example",
                    first_seen=seeded_at,
                    last_seen=seeded_at,
                    last_scanned=last_scanned,
                    scan_count=scan_count,
                    registry_source="test",
                    risk_tier="unknown",
                )
            )
        session.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/scan-age-histogram")
    def scan_age_histogram_route(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_scan_age_histogram(session)

    def override_get_session() -> Any:
        with testing_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/registry/scan-age-histogram")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_servers"] == len(test_servers)
    assert payload["never_scanned"] == 1
    assert payload["zero_scan_count"] == 1
    assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total_servers"]
    assert next(
        bucket["count"]
        for bucket in payload["buckets"]
        if bucket["label"] == "never-scanned"
    ) == 1

    print("PASS")