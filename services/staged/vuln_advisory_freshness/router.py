"""HTTP surface for advisory freshness reporting."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import advisory_freshness

router = APIRouter(prefix="/api", tags=["vuln_advisory_freshness"])


class AdvisoryFeedFreshness(BaseModel):
    feed: str
    advisory_count: int
    severity_counts: dict[str, int]
    ecosystems: list[str]
    oldest_published_at: datetime | None = None
    newest_published_at: datetime | None = None
    oldest_fetched_at: datetime | None = None
    newest_fetched_at: datetime | None = None
    stale_hours: float


class AdvisoryFreshnessResponse(BaseModel):
    feeds: list[AdvisoryFeedFreshness]


@router.get(
    "/vulns/advisory-freshness",
    response_model=AdvisoryFreshnessResponse,
)
def get_advisory_freshness(
    db: Session = Depends(get_session),
    now: datetime | None = None,
) -> AdvisoryFreshnessResponse:
    return AdvisoryFreshnessResponse.model_validate(
        advisory_freshness(db, now=now)
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import VulnAdvisory

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    fetched_at = datetime.now(timezone.utc)
    test_db.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="osv",
                summary="First advisory",
                severity="critical",
                ecosystem="PyPI",
                package="example-one",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.test/one",
                published_at=fetched_at,
                fetched_at=fetched_at,
                identities={},
                content_hash="one",
            ),
            VulnAdvisory(
                id=2,
                feed="osv",
                summary="Second advisory",
                severity="high",
                ecosystem="npm",
                package="example-two",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.test/two",
                published_at=fetched_at,
                fetched_at=fetched_at,
                identities={},
                content_hash="two",
            ),
            VulnAdvisory(
                id=3,
                feed="github",
                summary="Third advisory",
                severity="medium",
                ecosystem="npm",
                package="example-three",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.test/three",
                published_at=fetched_at,
                fetched_at=fetched_at,
                identities={},
                content_hash="three",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_db

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/vulns/advisory-freshness")
    assert response.status_code == 200
    payload = response.json()
    feeds = {item["feed"]: item for item in payload["feeds"]}
    assert feeds["osv"]["advisory_count"] == 2
    assert feeds["github"]["advisory_count"] == 1
    assert feeds["osv"]["severity_counts"] == {"critical": 1, "high": 1}
    assert feeds["github"]["severity_counts"] == {"medium": 1}
    assert all(item["stale_hours"] >= 0 for item in payload["feeds"])
    print("PASS")