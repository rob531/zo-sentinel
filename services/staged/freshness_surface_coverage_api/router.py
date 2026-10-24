from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat

from .logic import *  # noqa: F401,F403


router = APIRouter(prefix="/api")


class SurfaceOut(BaseModel):
    surface: str
    computed_at: str | None
    compute_seconds: float | None
    age_seconds: float | None
    stale: bool


class FreshnessSurfaceCoverageOut(BaseModel):
    surfaces: list[SurfaceOut]
    total: int
    stale_count: int


_COVERAGE_FUNCTION_NAMES = (
    "get_surface_coverage",
    "get_freshness_surface_coverage",
    "list_surface_coverage",
    "list_freshness_surfaces",
    "fetch_surface_coverage",
    "build_surface_coverage",
    "compute_surface_coverage",
    "get_surface_freshness",
    "freshness_surface_coverage",
)


def _coverage_provider() -> Callable[[Session], dict[str, Any]]:
    for name in _COVERAGE_FUNCTION_NAMES:
        candidate = globals().get(name)
        if callable(candidate):
            return candidate
    raise RuntimeError("The freshness surface coverage logic function is unavailable")


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfaceCoverageOut,
)
def list_freshness_surfaces(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return _coverage_provider()(session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    sqlite_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(sqlite_engine)
    testing_session_factory = sessionmaker(
        bind=sqlite_engine,
        autoflush=False,
        autocommit=False,
    )

    now = datetime.utcnow()
    threshold_payload = {
        "threshold": 60,
        "threshold_seconds": 60,
        "stale_after_seconds": 60,
        "max_age_seconds": 60,
        "ttl_seconds": 60,
    }

    with testing_session_factory() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="recent_surface",
                    payload=threshold_payload,
                    computed_at=now - timedelta(seconds=1),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="stale_surface",
                    payload=threshold_payload,
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="third_surface",
                    payload=threshold_payload,
                    computed_at=now - timedelta(seconds=5),
                    compute_seconds=0.5,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with testing_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/freshness/surfaces")
    response.raise_for_status()
    payload = response.json()

    assert payload["total"] == 3, payload
    assert payload["stale_count"] == 1, payload
    stale_surfaces = [item for item in payload["surfaces"] if item["stale"]]
    assert len(stale_surfaces) == 1, payload
    assert stale_surfaces[0]["surface"] == "stale_surface", payload

    print("PASS")