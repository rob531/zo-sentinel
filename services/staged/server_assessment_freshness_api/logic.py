from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_server_freshness(days: int = 30, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return server freshness buckets and stale-source counts."""

    axis_counts = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            func.count(McpLlmAxisScore.id).label("axis_score_count"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_assessed,
            func.coalesce(axis_counts.c.axis_score_count, 0).label("axis_score_count"),
        )
        .outerjoin(axis_counts, axis_counts.c.server_id == McpServerRegistry.server_id)
    )

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)

    bucket_order = ["fresh", "recent", "stale", "overdue", "never_assessed"]
    counts = {bucket: 0 for bucket in bucket_order}
    source_counts: dict[str, int] = {}

    for row in rows:
        assessed = _as_utc(row.last_assessed)
        if assessed is None:
            bucket = "never_assessed"
        else:
            age_hours = max(0.0, (now - assessed).total_seconds() / 3600.0)
            if age_hours < 24:
                bucket = "fresh"
            elif age_hours < 72:
                bucket = "recent"
            elif age_hours <= 168:
                bucket = "stale"
            else:
                bucket = "overdue"

        counts[bucket] += 1

        if bucket in {"stale", "overdue"}:
            source = row.registry_source or "unknown"
            source_counts[source] = source_counts.get(source, 0) + 1

    total_servers = len(rows)
    buckets = [
        {
            "bucket": bucket,
            "count": counts[bucket],
            "share": (counts[bucket] / total_servers) if total_servers else 0.0,
        }
        for bucket in bucket_order
    ]

    stale_sources = [
        {"registry_source": source, "stale_count": source_counts[source]}
        for source in sorted(source_counts)
    ]

    return {
        "days": int(days),
        "total_servers": total_servers,
        "buckets": buckets,
        "stale_sources": stale_sources,
        "generated_at": now.isoformat(),
    }


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seeded = [
        McpServerRegistry(
            server_id="fresh-1",
            name="fresh-1",
            registry_source="source-a",
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="recent-1",
            name="recent-1",
            registry_source="source-a",
            last_assessed=now - timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="stale-1",
            name="stale-1",
            registry_source="source-a",
            last_assessed=now - timedelta(days=10),
        ),
        McpServerRegistry(
            server_id="never-1",
            name="never-1",
            registry_source="source-b",
            last_assessed=None,
        ),
        McpServerRegistry(
            server_id="stale-2",
            name="stale-2",
            registry_source="source-b",
            last_assessed=now - timedelta(days=10),
        ),
    ]

    db = testing_session()
    try:
        db.add_all(seeded)
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/servers/freshness")
    def freshness(days: int = 30, session: Session = Depends(get_session)):
        return get_server_freshness(days=days, session=session)

    with TestClient(that_app) as client:
        response = client.get("/api/servers/freshness", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 5
        assert next(
            bucket for bucket in payload["buckets"] if bucket["bucket"] == "never_assessed"
        )["count"] == 1
        assert sum(item["stale_count"] for item in payload["stale_sources"]) == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()