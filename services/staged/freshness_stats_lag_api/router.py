from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat

if __package__:
    from .logic import get_freshness_stats_lag
else:
    from services.staged.freshness_stats_lag_api.logic import get_freshness_stats_lag


router = APIRouter(prefix="/api", tags=["freshness"])


class FreshnessSurfaceLag(BaseModel):
    surface: str
    computed_at: datetime
    compute_seconds: float
    age_seconds: float
    payload_keys: List[str]


class FreshnessStatsLagResponse(BaseModel):
    surfaces: List[FreshnessSurfaceLag]
    oldest_surface: Optional[str] = None
    newest_surface: Optional[str] = None


@router.get("/freshness/stats-lag", response_model=FreshnessStatsLagResponse)
def freshness_stats_lag(session: Session = Depends(get_session)) -> FreshnessStatsLagResponse:
    rows = get_freshness_stats_lag(session)
    surfaces = [FreshnessSurfaceLag(**row) for row in rows]
    surfaces.sort(key=lambda item: item.age_seconds, reverse=True)

    return FreshnessStatsLagResponse(
        surfaces=surfaces,
        oldest_surface=surfaces[0].surface if surfaces else None,
        newest_surface=surfaces[-1].surface if surfaces else None,
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with test_session_factory() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="newest",
                    payload={"health": "ok", "count": 3},
                    computed_at=now - timedelta(seconds=5),
                    compute_seconds=0.5,
                ),
                FreshnessStat(
                    surface="middle",
                    payload={"health": "ok"},
                    computed_at=now - timedelta(seconds=30),
                    compute_seconds=1.0,
                ),
                FreshnessStat(
                    surface="oldest",
                    payload={"health": "stale", "source": "pipeline"},
                    computed_at=now - timedelta(seconds=120),
                    compute_seconds=2.0,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/freshness/stats-lag")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert body["surfaces"][0]["surface"] == "oldest"
    assert all(isinstance(item["payload_keys"], list) for item in body["surfaces"])
    print("PASS")