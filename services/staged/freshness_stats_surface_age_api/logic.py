from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_as_object(value: Any) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if value is None:
        return {}
    return value


def get_surface_age(session: Session, request_time: datetime | None = None) -> dict[str, Any]:
    now = _utc_now() if request_time is None else _as_utc(request_time)
    rows = session.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface.asc())
    ).scalars().all()

    surfaces = []
    for row in rows:
        computed_at = _as_utc(row.computed_at)
        age_seconds = max(0.0, (now - computed_at).total_seconds())
        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": computed_at.isoformat(),
                "compute_seconds": float(row.compute_seconds or 0.0),
                "age_seconds": age_seconds,
                "payload": _payload_as_object(row.payload),
            }
        )

    return {
        "surfaces": surfaces,
        "generated_at": now.isoformat(),
    }


def get_surface_age_stats(session: Session, request_time: datetime | None = None) -> dict[str, Any]:
    return get_surface_age(session, request_time)


def get_freshness_surface_age(session: Session, request_time: datetime | None = None) -> dict[str, Any]:
    return get_surface_age(session, request_time)


def get_surface_age_distribution(session: Session, request_time: datetime | None = None) -> dict[str, Any]:
    return get_surface_age(session, request_time)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine)

    base_time = datetime.now(timezone.utc)
    seeded = [
        {
            "surface": "alpha",
            "payload": {"kind": "alpha", "value": 1},
            "computed_at": base_time - timedelta(seconds=10),
            "compute_seconds": 0.25,
        },
        {
            "surface": "beta",
            "payload": {"kind": "beta", "value": 2},
            "computed_at": base_time - timedelta(seconds=20),
            "compute_seconds": 0.5,
        },
        {
            "surface": "gamma",
            "payload": {"kind": "gamma", "value": 3},
            "computed_at": base_time - timedelta(seconds=30),
            "compute_seconds": 0.75,
        },
    ]

    with TestingSession() as session:
        session.add_all([FreshnessStat(**row) for row in seeded])
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/freshness/surface-age")
    def surface_age(session: Session = Depends(get_session)):
        return get_surface_age(session)

    response = TestClient(test_app).get("/api/freshness/surface-age")
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    by_surface = {item["surface"]: item for item in body["surfaces"]}
    assert by_surface["alpha"]["payload"] == {"kind": "alpha", "value": 1}
    assert all(item["age_seconds"] >= 0 for item in body["surfaces"])
    print("PASS")