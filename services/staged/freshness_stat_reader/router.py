"""HTTP surface for freshness statistics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_stats

router = APIRouter(prefix="/api/freshness", tags=["freshness"])


class FreshnessSurfaceResponse(BaseModel):
    surface: str
    payload: Any
    computed_at: datetime
    compute_seconds: float | None
    age_seconds: float
    stale: bool


class FreshnessSurfacesResponse(BaseModel):
    count: int
    generated_at: datetime
    surfaces: list[FreshnessSurfaceResponse]


@router.get("/surfaces", response_model=FreshnessSurfacesResponse)
def list_freshness_surfaces(
    sla_seconds: int = Query(default=3600, ge=0),
    db: Session = Depends(get_session),
) -> FreshnessSurfacesResponse:
    rows = get_freshness_stats(db, sla_seconds)
    surfaces = [FreshnessSurfaceResponse.model_validate(row) for row in rows]
    return FreshnessSurfacesResponse(
        count=len(surfaces),
        generated_at=datetime.now(timezone.utc),
        surfaces=surfaces,
    )


def _self_test() -> None:
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import FreshnessStat

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as session:
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                FreshnessStat(
                    surface="old",
                    payload={"value": 1},
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface="fresh-a",
                    payload={"value": 2},
                    computed_at=now - timedelta(seconds=30),
                    compute_seconds=0.10,
                ),
                FreshnessStat(
                    surface="fresh-b",
                    payload={"value": 3},
                    computed_at=now - timedelta(seconds=60),
                    compute_seconds=0.15,
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = lambda: session

        response = TestClient(test_app).get(
            "/api/freshness/surfaces",
            params={"sla_seconds": 3600},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == 3, body
        stale = [surface for surface in body["surfaces"] if surface["stale"]]
        assert len(stale) == 1, body
        assert stale[0]["age_seconds"] > 0, body

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()