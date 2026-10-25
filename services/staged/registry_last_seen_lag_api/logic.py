from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_NAMES = (
    "fresh_1h",
    "fresh_24h",
    "stale_7d",
    "stale_30d",
    "dormant_90d",
)


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        value = datetime.fromisoformat(text)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_lag(lag_hours: float) -> str:
    if lag_hours <= 1:
        return "fresh_1h"
    if lag_hours <= 24:
        return "fresh_24h"
    if lag_hours <= 24 * 7:
        return "stale_7d"
    if lag_hours <= 24 * 30:
        return "stale_30d"
    return "dormant_90d"


async def get_last_seen_lag(
    hours: int = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.last_seen,
        )
    )
    if hasattr(result, "__await__"):
        result = await result

    rows = result.all()
    normalized_rows: list[tuple[str, str | None, datetime]] = []

    for server_id, name, last_seen in rows:
        timestamp = _utc_datetime(last_seen)
        if timestamp is None:
            continue
        normalized_rows.append(
            (
                str(server_id),
                str(name) if name is not None else None,
                timestamp,
            )
        )

    buckets = {name: 0 for name in _BUCKET_NAMES}
    servers: list[dict[str, Any]] = []

    if normalized_rows:
        newest_last_seen = max(row[2] for row in normalized_rows)
        for server_id, name, last_seen in normalized_rows:
            lag_hours = round(
                max(0.0, (newest_last_seen - last_seen).total_seconds() / 3600),
                2,
            )
            bucket = _bucket_for_lag(lag_hours)
            buckets[bucket] += 1
            servers.append(
                {
                    "server_id": server_id,
                    "name": name,
                    "last_seen": last_seen,
                    "lag_hours": lag_hours,
                    "bucket": bucket,
                }
            )

    servers.sort(key=lambda item: (-item["lag_hours"], item["server_id"]))

    return {
        "hours": hours,
        "summary": {
            "total": len(servers),
            "buckets": buckets,
        },
        "servers": servers[:200],
    }


async def get_registry_last_seen_lag(
    hours: int = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return await get_last_seen_lag(hours=hours, session=session)


async def get_last_seen_lag_data(
    hours: int = 0,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return await get_last_seen_lag(hours=hours, session=session)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(that_engine)
    ThatSession = sessionmaker(bind=that_engine, expire_on_commit=False)

    newest = datetime.now(timezone.utc).replace(tzinfo=None)
    seeded = [
        ("seed-fresh", "Fresh server", newest - timedelta(hours=1)),
        ("seed-day", "One day server", newest - timedelta(hours=48)),
        ("seed-ten-day", "Ten day server", newest - timedelta(days=10)),
        ("seed-sixty-day", "Sixty day server", newest - timedelta(days=60)),
        ("seed-dormant", "Dormant server", newest - timedelta(days=200)),
    ]

    with ThatSession() as that_session:
        that_session.add_all(
            [
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    last_seen=last_seen,
                )
                for server_id, name, last_seen in seeded
            ]
        )
        that_session.commit()

    def override_get_session():
        with ThatSession() as that_session:
            yield that_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/last-seen-lag")
    async def last_seen_lag_endpoint(
        hours: int = 0,
        session: Session = Depends(get_session),
    ):
        return await get_last_seen_lag(hours=hours, session=session)

    with TestClient(that_app) as client:
        response = client.get("/api/registry/last-seen-lag?hours=0")
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["buckets"]["fresh_1h"] >= 1
        assert payload["summary"]["buckets"]["dormant_90d"] >= 1
        assert payload["summary"]["buckets"]["stale_30d"] == 1

    print("PASS")