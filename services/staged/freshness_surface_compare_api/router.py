from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat

from .logic import compare_freshness


router = APIRouter(prefix="/api", tags=["freshness"])


class FreshnessCompareQuery(BaseModel):
    surface: Optional[str] = Field(default=None)


class SurfaceFreshness(BaseModel):
    surface: str
    computed_at: datetime
    age_seconds: float
    compute_seconds: float
    status: Literal["fresh", "aging", "stale"]


class StaleSurface(BaseModel):
    surface: str
    age_seconds: float


class FreshnessCompareResponse(BaseModel):
    surfaces: list[SurfaceFreshness]
    stale: list[StaleSurface]


def freshness_compare_query(
    surface: Optional[str] = Query(default=None),
) -> FreshnessCompareQuery:
    return FreshnessCompareQuery(surface=surface)


@router.get(
    "/freshness/compare",
    response_model=FreshnessCompareResponse,
    summary="Compare freshness across surfaces",
)
def compare_freshness_surfaces(
    params: FreshnessCompareQuery = Depends(freshness_compare_query),
    session: Session = Depends(get_session),
) -> FreshnessCompareResponse:
    result = compare_freshness(session, params.surface)
    return FreshnessCompareResponse.model_validate(result)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session() -> Generator[Session, None, None]:
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="alpha",
                    payload={},
                    computed_at=now - timedelta(seconds=60),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="beta",
                    payload={},
                    computed_at=now - timedelta(seconds=600),
                    compute_seconds=2.5,
                ),
                FreshnessStat(
                    surface="gamma",
                    payload={},
                    computed_at=now - timedelta(seconds=7200),
                    compute_seconds=3.5,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/freshness/compare")
        assert response.status_code == 200
        body = response.json()
        assert len(body["surfaces"]) == 3
        assert body["stale"] == [
            {
                "surface": "gamma",
                "age_seconds": body["stale"][0]["age_seconds"],
            }
        ]
        assert body["stale"][0]["age_seconds"] > 3600

    print("PASS")


if __name__ == "__main__":
    _self_test()