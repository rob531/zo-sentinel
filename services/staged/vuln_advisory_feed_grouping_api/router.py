from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory
from .logic import get_feed_grouping

router = APIRouter(prefix="/api")


class SeverityMix(BaseModel):
    low: int = 0
    medium: int = 0
    high: int = 0
    critical: int = 0


class FeedRollup(BaseModel):
    feed: str
    count: int
    latest_published_at: Optional[datetime] = None
    oldest_published_at: Optional[datetime] = None
    severity_mix: SeverityMix
    ecosystem_count: int


class AdvisoryFeedTotals(BaseModel):
    advisories: int
    feeds: int


class AdvisoryFeedGroupingResponse(BaseModel):
    feeds: List[FeedRollup]
    totals: AdvisoryFeedTotals


@router.get(
    "/vuln/advisories/feeds",
    response_model=AdvisoryFeedGroupingResponse,
)
def list_advisory_feed_grouping(
    limit: Optional[int] = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> AdvisoryFeedGroupingResponse:
    return get_feed_grouping(session, limit)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    rows = [
        VulnAdvisory(
            id=1,
            feed="osv",
            summary="First advisory",
            severity="high",
            ecosystem="PyPI",
            package="example-one",
            affected_ranges=None,
            aliases=None,
            source_url="https://example.test/1",
            published_at=datetime(2025, 1, 1),
            fetched_at=datetime(2025, 1, 2),
            identities=None,
            content_hash="hash-1",
        ),
        VulnAdvisory(
            id=2,
            feed="osv",
            summary="Second advisory",
            severity="critical",
            ecosystem="npm",
            package="example-two",
            affected_ranges=None,
            aliases=None,
            source_url="https://example.test/2",
            published_at=datetime(2025, 1, 3),
            fetched_at=datetime(2025, 1, 4),
            identities=None,
            content_hash="hash-2",
        ),
        VulnAdvisory(
            id=3,
            feed="github",
            summary="Third advisory",
            severity="medium",
            ecosystem="PyPI",
            package="example-three",
            affected_ranges=None,
            aliases=None,
            source_url="https://example.test/3",
            published_at=datetime(2025, 1, 2),
            fetched_at=datetime(2025, 1, 3),
            identities=None,
            content_hash="hash-3",
        ),
        VulnAdvisory(
            id=4,
            feed="github",
            summary="Fourth advisory",
            severity="low",
            ecosystem="npm",
            package="example-four",
            affected_ranges=None,
            aliases=None,
            source_url="https://example.test/4",
            published_at=datetime(2025, 1, 4),
            fetched_at=datetime(2025, 1, 5),
            identities=None,
            content_hash="hash-4",
        ),
    ]
    testing_session.add_all(rows)
    testing_session.commit()

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisories/feeds")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["feeds"]) == 2
        assert payload["totals"]["advisories"] == 4
        assert any(feed["feed"] == "osv" and feed["count"] == 2 for feed in payload["feeds"])

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()