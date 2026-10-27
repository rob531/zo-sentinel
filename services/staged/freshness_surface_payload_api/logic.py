from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

STALE_AFTER_SECONDS = 3600


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _decode_payload(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def get_freshness_surfaces(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stats = session.execute(select(FreshnessStat)).scalars().all()

    latest: dict[str, FreshnessStat] = {}
    for stat in stats:
        previous = latest.get(stat.surface)
        if previous is None:
            latest[stat.surface] = stat
            continue

        current_time = (
            _utc_naive(stat.computed_at)
            if stat.computed_at is not None
            else datetime.min
        )
        previous_time = (
            _utc_naive(previous.computed_at)
            if previous.computed_at is not None
            else datetime.min
        )
        if current_time > previous_time:
            latest[stat.surface] = stat

    surfaces = [
        {
            "surface": stat.surface,
            "computed_at": (
                stat.computed_at.isoformat() if stat.computed_at is not None else None
            ),
            "compute_seconds": stat.compute_seconds,
            "payload": _decode_payload(stat.payload),
        }
        for stat in sorted(latest.values(), key=lambda item: item.surface)
    ]

    newest = max(
        (
            _utc_naive(stat.computed_at)
            for stat in latest.values()
            if stat.computed_at is not None
        ),
        default=None,
    )
    stale_surfaces: list[str] = []
    if newest is not None:
        cutoff = newest - timedelta(seconds=STALE_AFTER_SECONDS)
        stale_surfaces = sorted(
            stat.surface
            for stat in latest.values()
            if stat.computed_at is not None
            and _utc_naive(stat.computed_at) < cutoff
        )

    return {"surfaces": surfaces, "stale_surfaces": stale_surfaces}


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
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    FreshnessStat.__table__.create(engine)

    newest = datetime(2025, 1, 1, 12, 0, 0)
    with test_session() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="alpha",
                    payload={"value": 1},
                    computed_at=newest,
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="beta",
                    payload={"value": 2},
                    computed_at=newest - timedelta(minutes=30),
                    compute_seconds=2.5,
                ),
                FreshnessStat(
                    surface="gamma",
                    payload={"value": 3},
                    computed_at=newest - timedelta(hours=2),
                    compute_seconds=3.5,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with test_session() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/freshness/surfaces")
    def freshness_surfaces(data: dict[str, Any] = Depends(get_freshness_surfaces)):
        return data

    response = TestClient(that_app).get("/api/freshness/surfaces")
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert body["stale_surfaces"]
    print("PASS")


if __name__ == "__main__":
    _self_test()