from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from .logic import get_freshness_rollup


router = APIRouter(prefix="/api")


class FeedFreshness(BaseModel):
    feed: str
    advisory_count: int
    linked_count: int
    oldest_published_at: str | None
    latest_published_at: str | None
    stale_count: int


class FreshnessRollupResponse(BaseModel):
    by_feed: list[FeedFreshness]
    by_severity: dict[str, int]
    by_ecosystem: dict[str, int]
    unlinked: int
    totals: dict[str, int]


@router.get("/vuln/advisories/freshness", response_model=FreshnessRollupResponse)
def advisory_freshness_rollup(
    session: Session = Depends(get_session),
) -> dict:
    return get_freshness_rollup(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="linked-feed",
                    severity="high",
                    ecosystem="pypi",
                    package="example-package",
                    published_at=now - timedelta(days=10),
                    fetched_at=now,
                    content_hash="hash-1",
                    source_url="https://example.test/advisory/1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="linked-feed",
                    severity="medium",
                    ecosystem="npm",
                    package="another-package",
                    published_at=now - timedelta(days=120),
                    fetched_at=now,
                    content_hash="hash-2",
                    source_url="https://example.test/advisory/2",
                ),
                VulnAdvisory(
                    id=3,
                    feed="other-feed",
                    severity="low",
                    ecosystem=None,
                    package="third-package",
                    published_at=now - timedelta(days=5),
                    fetched_at=now,
                    content_hash="hash-3",
                    source_url="https://example.test/advisory/3",
                ),
            ]
        )
        session.add(
            VulnLink(
                id=1,
                advisory_id=1,
                server_id="server-1",
                match_basis="package",
                match_value="example-package",
                match_confidence=0.95,
            )
        )
        session.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/vuln/advisories/freshness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["advisories"] == 3
        assert payload["totals"]["links"] == 1
        assert len(payload["by_feed"]) == 2
        linked_feed = next(
            item for item in payload["by_feed"] if item["feed"] == "linked-feed"
        )
        assert linked_feed["linked_count"] == 1
        assert payload["unlinked"] == 2

    print("PASS")