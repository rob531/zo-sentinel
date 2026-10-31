from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


class SurfaceStatus(BaseModel):
    surface: str
    computed_at: datetime
    compute_seconds: float
    payload_keys: int
    age_seconds: int
    staleness: str


class FreshnessSurfaceResponse(BaseModel):
    generated_at: datetime
    surfaces: list[SurfaceStatus]
    counts: dict[str, int]
    worst_surface: str | None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _classify_age(age_seconds: int) -> str:
    if age_seconds <= 3600:
        return "fresh"
    if age_seconds <= 6 * 3600:
        return "aging"
    return "stale"


def get_freshness_surface_status(session: Session) -> FreshnessSurfaceResponse:
    generated_at = datetime.now(timezone.utc)
    rows = session.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
            FreshnessStat.payload,
        )
    ).all()

    surfaces: list[SurfaceStatus] = []
    counts = {"fresh": 0, "aging": 0, "stale": 0}
    worst_surface: str | None = None
    worst_age = -1

    for surface, computed_at, compute_seconds, payload in rows:
        computed_at_utc = _as_utc(computed_at)
        age_seconds = max(0, int((generated_at - computed_at_utc).total_seconds()))
        staleness = _classify_age(age_seconds)
        counts[staleness] += 1

        if age_seconds > worst_age:
            worst_age = age_seconds
            worst_surface = surface

        if isinstance(payload, dict):
            payload_keys = len(payload)
        elif isinstance(payload, (list, tuple)):
            payload_keys = len(payload)
        else:
            payload_keys = 0

        surfaces.append(
            SurfaceStatus(
                surface=surface,
                computed_at=computed_at,
                compute_seconds=float(compute_seconds or 0),
                payload_keys=payload_keys,
                age_seconds=age_seconds,
                staleness=staleness,
            )
        )

    surfaces.sort(key=lambda item: item.surface)
    return FreshnessSurfaceResponse(
        generated_at=generated_at,
        surfaces=surfaces,
        counts=counts,
        worst_surface=worst_surface,
    )


def _build_test_app(session: Session) -> FastAPI:
    test_app = FastAPI()

    @test_app.get(
        "/api/freshness/surfaces",
        response_model=FreshnessSurfaceResponse,
    )
    def freshness_surfaces(db: Session = Depends(get_session)) -> FreshnessSurfaceResponse:
        return get_freshness_surface_status(db)

    test_app.dependency_overrides[get_session] = lambda: session
    return test_app


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    FreshnessStat.__table__.create(bind=engine)
    session = TestingSession()

    try:
        now = datetime.now(timezone.utc)
        seed_rows = [
            FreshnessStat(
                surface="alpha",
                payload={"one": 1, "two": 2},
                computed_at=now - timedelta(minutes=30),
                compute_seconds=1.25,
            ),
            FreshnessStat(
                surface="beta",
                payload={"three": 3},
                computed_at=now - timedelta(hours=2),
                compute_seconds=2.5,
            ),
            FreshnessStat(
                surface="gamma",
                payload={"four": 4, "five": 5, "six": 6},
                computed_at=now - timedelta(hours=7),
                compute_seconds=3.75,
            ),
        ]
        session.add_all(seed_rows)
        session.commit()

        test_app = _build_test_app(session)
        with TestClient(test_app) as client:
            response = client.get("/api/freshness/surfaces")

        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        assert len(body["surfaces"]) == 3, body
        assert sum(body["counts"].values()) == 3, body
        assert body["counts"]["stale"] == 1, body
    finally:
        session.close()
        engine.dispose()


def main() -> int:
    run_contract()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())