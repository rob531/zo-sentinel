from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


STALE_THRESHOLD_SECONDS = 3600


class SurfaceFreshness(BaseModel):
    surface: str
    computed_at: datetime
    compute_seconds: float
    stale: bool
    payload: Dict[str, Any]


class FreshnessCounts(BaseModel):
    total: int
    stale: int
    fresh: int


class FreshnessSurfacesResponse(BaseModel):
    generated_at: datetime
    stale_threshold_seconds: int
    surfaces: List[SurfaceFreshness]
    counts: FreshnessCounts


router = APIRouter(prefix="/api")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def get_freshness_surfaces(session: Session) -> FreshnessSurfacesResponse:
    generated_at = datetime.now(timezone.utc)
    latest = (
        session.query(
            FreshnessStat.surface.label("surface"),
            func.max(FreshnessStat.computed_at).label("computed_at"),
        )
        .group_by(FreshnessStat.surface)
        .subquery()
    )

    records = (
        session.query(FreshnessStat)
        .join(
            latest,
            (FreshnessStat.surface == latest.c.surface)
            & (FreshnessStat.computed_at == latest.c.computed_at),
        )
        .order_by(FreshnessStat.surface)
        .all()
    )

    surfaces: List[SurfaceFreshness] = []
    seen = set()
    for record in records:
        if record.surface in seen:
            continue
        seen.add(record.surface)

        computed_at = _as_utc(record.computed_at)
        stale = generated_at - computed_at > timedelta(
            seconds=STALE_THRESHOLD_SECONDS
        )
        surfaces.append(
            SurfaceFreshness(
                surface=record.surface,
                computed_at=computed_at,
                compute_seconds=float(record.compute_seconds or 0.0),
                stale=stale,
                payload=_parse_payload(record.payload),
            )
        )

    stale_count = sum(1 for surface in surfaces if surface.stale)
    return FreshnessSurfacesResponse(
        generated_at=generated_at,
        stale_threshold_seconds=STALE_THRESHOLD_SECONDS,
        surfaces=surfaces,
        counts=FreshnessCounts(
            total=len(surfaces),
            stale=stale_count,
            fresh=len(surfaces) - stale_count,
        ),
    )


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfacesResponse,
)
def freshness_surfaces(
    session: Session = Depends(get_session),
) -> FreshnessSurfacesResponse:
    return get_freshness_surfaces(session)


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


app = create_app()


def _run_self_test() -> None:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    FreshnessStat.__table__.create(test_engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="scanner",
                    payload={"kind": "scan", "healthy": True},
                    computed_at=now,
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="scorer",
                    payload={"kind": "score", "healthy": True},
                    computed_at=now,
                    compute_seconds=0.5,
                ),
                FreshnessStat(
                    surface="intel",
                    payload={"kind": "intel", "healthy": False},
                    computed_at=now - timedelta(days=3650),
                    compute_seconds=12.0,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        database = TestingSessionLocal()
        try:
            yield database
        finally:
            database.close()

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/freshness/surfaces")
            assert response.status_code == 200
            body = response.json()
            assert len(body["surfaces"]) == 3
            assert sum(1 for row in body["surfaces"] if row["stale"]) == 1
    finally:
        test_app.dependency_overrides.clear()
        test_engine.dispose()

    print("PASS")


def run_self_test() -> None:
    _run_self_test()


def main() -> None:
    _run_self_test()


if __name__ == "__main__":
    main()