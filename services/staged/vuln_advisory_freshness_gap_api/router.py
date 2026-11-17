from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from .logic import get_freshness_gap

router = APIRouter(prefix="/api", tags=["vulnerability-advisories"])


class FreshnessGapFeed(BaseModel):
    feed: str
    total: int
    unlinked: int
    stale: int
    median_staleness_hours: float


class FreshnessGapResponse(BaseModel):
    days: int
    feeds: list[FreshnessGapFeed]


@router.get(
    "/vuln/advisories/freshness-gap",
    response_model=FreshnessGapResponse,
)
def advisory_freshness_gap(
    days: int = Query(default=7, ge=1),
    session: Session = Depends(get_session),
) -> FreshnessGapResponse:
    return get_freshness_gap(session, days)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)

    now = datetime.utcnow()
    with SessionLocal() as session:
        advisories = [
            VulnAdvisory(
                id=1,
                feed="seeded",
                source_url="https://example.test/advisory/1",
                severity="high",
                ecosystem="pypi",
                published_at=now - timedelta(days=31),
                fetched_at=now - timedelta(days=30),
            ),
            VulnAdvisory(
                id=2,
                feed="seeded",
                source_url="https://example.test/advisory/2",
                severity="high",
                ecosystem="npm",
                published_at=now - timedelta(days=31),
                fetched_at=now - timedelta(days=30),
            ),
            VulnAdvisory(
                id=3,
                feed="seeded",
                source_url="https://example.test/advisory/3",
                severity="medium",
                ecosystem="pypi",
                published_at=now - timedelta(days=31),
                fetched_at=now - timedelta(days=30),
            ),
            VulnAdvisory(
                id=4,
                feed="secondary",
                source_url="https://example.test/advisory/4",
                severity="low",
                ecosystem="go",
                published_at=now - timedelta(days=2),
                fetched_at=now - timedelta(days=1),
            ),
            VulnAdvisory(
                id=5,
                feed="secondary",
                source_url="https://example.test/advisory/5",
                severity="medium",
                ecosystem="npm",
                published_at=now - timedelta(days=2),
                fetched_at=now - timedelta(days=1),
            ),
            VulnAdvisory(
                id=6,
                feed="secondary",
                source_url="https://example.test/advisory/6",
                severity="low",
                ecosystem="go",
                published_at=now - timedelta(days=2),
                fetched_at=now - timedelta(days=1),
            ),
            VulnLink(
                id=1,
                advisory_id=1,
                server_id=1,
                match_basis="package",
                match_value="example-package",
                match_confidence=1.0,
            ),
        ]
        session.add_all(advisories)
        session.commit()

    def override_session() -> Iterator[Session]:
        with SessionLocal() as session:
            yield session

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get(
        "/api/vuln/advisories/freshness-gap",
        params={"days": 7},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["feeds"]) == 2

    seeded = next(item for item in payload["feeds"] if item["feed"] == "seeded")
    assert seeded["unlinked"] == 2
    assert seeded["stale"] >= 3

    print("PASS")


if __name__ == "__main__":
    _self_test()