from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import get_feed_freshness


router = APIRouter(
    prefix="/api/vuln/advisories",
    tags=["vulnerability-advisories"],
)


class FeedFreshnessItem(BaseModel):
    feed: str
    advisories: int
    last_published_at: Optional[datetime]
    last_fetched_at: Optional[datetime]
    staleness_days: float
    max_lag_days: float


class FeedFreshnessResponse(BaseModel):
    feeds: list[FeedFreshnessItem]
    generated_at: datetime


@router.get("/feed-freshness", response_model=FeedFreshnessResponse)
def feed_freshness(
    feed: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = get_feed_freshness(session, feed, days)
    return FeedFreshnessResponse(**result).model_dump()


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    assert VulnAdvisory.__tablename__ == "vuln_advisories"

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT NOT NULL,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP,
                    content_hash TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        rows = [
            {
                "id": 1,
                "feed": "alpha",
                "severity": "high",
                "ecosystem": "pypi",
                "package": "alpha-one",
                "published_at": now - timedelta(days=3),
                "fetched_at": now - timedelta(days=1),
                "content_hash": "alpha-one-hash",
            },
            {
                "id": 2,
                "feed": "alpha",
                "severity": "medium",
                "ecosystem": "npm",
                "package": "alpha-two",
                "published_at": now - timedelta(days=2),
                "fetched_at": now - timedelta(hours=12),
                "content_hash": "alpha-two-hash",
            },
            {
                "id": 3,
                "feed": "beta",
                "severity": "critical",
                "ecosystem": "pypi",
                "package": "beta-one",
                "published_at": now - timedelta(days=1),
                "fetched_at": now - timedelta(hours=6),
                "content_hash": "beta-one-hash",
            },
        ]
        for row in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO vuln_advisories (
                        id, feed, severity, ecosystem, package,
                        published_at, fetched_at, content_hash
                    ) VALUES (
                        :id, :feed, :severity, :ecosystem, :package,
                        :published_at, :fetched_at, :content_hash
                    )
                    """
                ),
                row,
            )

    testing_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db = testing_session_factory()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/vuln/advisories/feed-freshness",
            params={"days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["feeds"]) == 2
        counts = {item["feed"]: item["advisories"] for item in payload["feeds"]}
        assert counts == {"alpha": 2, "beta": 1}
        assert all(item["staleness_days"] >= 0 for item in payload["feeds"])

    print("PASS")