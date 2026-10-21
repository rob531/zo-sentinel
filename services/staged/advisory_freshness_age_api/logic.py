from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/api")
advisory_freshness_router = router


class FeedFreshness(BaseModel):
    feed: str
    total: int = Field(ge=0)
    fresh: int = Field(ge=0)
    stale: int = Field(ge=0)
    oldest_published_at: str | None
    newest_published_at: str | None
    newest_fetched_at: str | None
    max_age_hours: int
    stale_fraction: float = Field(ge=0.0, le=1.0)


class FreshnessSummary(BaseModel):
    feeds: int = Field(ge=0)
    stale_feeds: int = Field(ge=0)


class AdvisoryFreshnessResponse(BaseModel):
    generated_at: str
    feeds: list[FeedFreshness]
    summary: FreshnessSummary


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_advisory_freshness(
    feed: str | None = Query(default=None),
    max_age_hours: int = Query(default=72, ge=0),
    session=Depends(get_session),
) -> AdvisoryFreshnessResponse:
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=max_age_hours)

    fresh_count = func.sum(
        case((VulnAdvisory.published_at >= cutoff, 1), else_=0)
    ).label("fresh")
    stale_count = func.sum(
        case(
            (VulnAdvisory.published_at.is_(None), 1),
            (VulnAdvisory.published_at < cutoff, 1),
            else_=0,
        )
    ).label("stale")

    statement = select(
        VulnAdvisory.feed.label("feed"),
        func.count(VulnAdvisory.id).label("total"),
        fresh_count,
        stale_count,
        func.min(VulnAdvisory.published_at).label("oldest_published_at"),
        func.max(VulnAdvisory.published_at).label("newest_published_at"),
        func.max(VulnAdvisory.fetched_at).label("newest_fetched_at"),
    )
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)
    statement = statement.group_by(VulnAdvisory.feed).order_by(VulnAdvisory.feed)

    rows = session.execute(statement).mappings().all()
    feeds: list[FeedFreshness] = []
    for row in rows:
        total = int(row["total"] or 0)
        fresh = int(row["fresh"] or 0)
        stale = int(row["stale"] or 0)
        feeds.append(
            FeedFreshness(
                feed=str(row["feed"]),
                total=total,
                fresh=fresh,
                stale=stale,
                oldest_published_at=_iso_utc(row["oldest_published_at"]),
                newest_published_at=_iso_utc(row["newest_published_at"]),
                newest_fetched_at=_iso_utc(row["newest_fetched_at"]),
                max_age_hours=max_age_hours,
                stale_fraction=(stale / total) if total else 0.0,
            )
        )

    return AdvisoryFreshnessResponse(
        generated_at=_iso_utc(generated_at) or generated_at.isoformat(),
        feeds=feeds,
        summary=FreshnessSummary(
            feeds=len(feeds),
            stale_feeds=sum(1 for item in feeds if item.stale > 0),
        ),
    )


@router.get(
    "/advisories/freshness",
    response_model=AdvisoryFreshnessResponse,
    summary="Report advisory-feed freshness and age",
)
def advisory_freshness_endpoint(
    feed: str | None = Query(default=None),
    max_age_hours: int = Query(default=72, ge=0),
    session=Depends(get_session),
) -> AdvisoryFreshnessResponse:
    return get_advisory_freshness(
        feed=feed,
        max_age_hours=max_age_hours,
        session=session,
    )


def build_router() -> APIRouter:
    return router


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    advisory_rows = [
        VulnAdvisory(
            id=1,
            feed="fresh-feed",
            severity="HIGH",
            ecosystem="PyPI",
            package="fresh-package",
            published_at=now - timedelta(hours=1),
            fetched_at=now - timedelta(minutes=30),
            content_hash="fresh-hash-1",
            source_url="https://example.invalid/advisory/1",
            summary="Fresh advisory one",
        ),
        VulnAdvisory(
            id=2,
            feed="fresh-feed",
            severity="MEDIUM",
            ecosystem="npm",
            package="fresh-package-two",
            published_at=now - timedelta(hours=2),
            fetched_at=now - timedelta(minutes=20),
            content_hash="fresh-hash-2",
            source_url="https://example.invalid/advisory/2",
            summary="Fresh advisory two",
        ),
        VulnAdvisory(
            id=3,
            feed="stale-feed",
            severity="LOW",
            ecosystem="PyPI",
            package="stale-package",
            published_at=now - timedelta(hours=100),
            fetched_at=now - timedelta(hours=99),
            content_hash="stale-hash",
            source_url="https://example.invalid/advisory/3",
            summary="Stale advisory",
        ),
    ]

    with TestSession() as session:
        session.add_all(advisory_rows)
        session.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get(
            "/api/advisories/freshness",
            params={"max_age_hours": 72},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["feeds"]) == 2, payload
        stale_feed = next(item for item in payload["feeds"] if item["feed"] == "stale-feed")
        assert stale_feed["stale"] == 1, stale_feed
        assert stale_feed["fresh"] == 0, stale_feed
        assert payload["summary"]["stale_feeds"] == 1, payload

    print("PASS")