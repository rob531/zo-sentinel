"""router.py -- freshness surface SLA coverage HTTP surface.

Exposes `router` for the staged freshness_surface_sla_coverage_api service.
Thin: injects the real Session via Depends(get_session) and delegates to the
relative service logic module.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

from .logic import freshness_surface_sla_coverage

router = APIRouter(
    prefix="/api/freshness/surfaces",
    tags=["freshness"],
)


class SurfaceCoverageItem(BaseModel):
    surface: str
    computed_at: datetime
    age_seconds: float
    compute_seconds: float
    stale: bool
    payload_keys: list[str]


class SurfaceCoverageResponse(BaseModel):
    generated_at: datetime
    stale_after_hours: int
    surfaces: list[SurfaceCoverageItem]


@router.get("/coverage", response_model=SurfaceCoverageResponse)
def get_surface_coverage(
    stale_after_hours: int = Query(default=24, ge=0),
    db: Session = Depends(get_session),
) -> SurfaceCoverageResponse:
    coverage = freshness_surface_sla_coverage(
        db,
        stale_after_hours=stale_after_hours,
    )
    return SurfaceCoverageResponse(**coverage)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    test_session = TestSession()
    try:
        test_session.add_all(
            [
                FreshnessStat(
                    surface="surface_fresh",
                    payload={
                        "rows": [{"id": 1}],
                        "stale_after_hours": 24,
                    },
                    computed_at=now - timedelta(hours=1),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="surface_stale",
                    payload={
                        "rows": [{"id": 2}],
                        "stale_after_hours": 24,
                    },
                    computed_at=now - timedelta(hours=48),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="surface_recent",
                    payload={
                        "rows": [{"id": 3}],
                        "stale_after_hours": 24,
                    },
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.5,
                ),
            ]
        )
        test_session.commit()
    finally:
        test_session.close()

    response = TestClient(test_app).get(
        "/api/freshness/surfaces/coverage",
        params={"stale_after_hours": 24},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert sum(item["stale"] is True for item in body["surfaces"]) == 1
    assert all(
        "surface" in item
        and "computed_at" in item
        and "age_seconds" in item
        for item in body["surfaces"]
    )
    print("PASS")