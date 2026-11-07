"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import freshness_sla_rollup

router = APIRouter(prefix="/api", tags=["freshness"])


class FreshnessSurfaceRollup(BaseModel):
    surface: str
    computed_at: datetime | None = None
    compute_seconds: float | None = None
    age_seconds: float | None = None
    payload: Any = None
    status: str


class FreshnessSlaCounts(BaseModel):
    fresh: int
    stale: int
    missing: int


class FreshnessSlaRollupResponse(BaseModel):
    generated_at: datetime
    threshold_seconds: int
    surfaces: list[FreshnessSurfaceRollup]
    counts: FreshnessSlaCounts


@router.get(
    "/freshness/sla-rollup",
    response_model=FreshnessSlaRollupResponse,
)
def get_freshness_sla_rollup(
    surface: str | None = Query(default=None),
    max_age_seconds: int = Query(default=3600, ge=1, le=86400),
    db: Session = Depends(get_session),
) -> FreshnessSlaRollupResponse:
    result = freshness_sla_rollup(
        db,
        surface=surface,
        max_age_seconds=max_age_seconds,
    )
    return FreshnessSlaRollupResponse.model_validate(result)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import FreshnessStat

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    FreshnessStat.__table__.create(bind=test_engine)
    session = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            FreshnessStat(
                surface="fresh-surface",
                payload={"value": 1},
                computed_at=now,
                compute_seconds=60.0,
            ),
            FreshnessStat(
                surface="stale-surface",
                payload={"value": 2},
                computed_at=now - timedelta(hours=2),
                compute_seconds=60.0,
            ),
            FreshnessStat(
                surface="filtered-surface",
                payload={"value": 3},
                computed_at=now,
                compute_seconds=60.0,
            ),
        ]
    )
    session.commit()
    session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get(
        "/api/freshness/sla-rollup",
        params={"max_age_seconds": 3600},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    statuses = {item["surface"]: item["status"] for item in body["surfaces"]}
    assert statuses["fresh-surface"] == "fresh"
    assert statuses["stale-surface"] == "stale"
    assert body["counts"]["stale"] >= 1
    print("PASS")