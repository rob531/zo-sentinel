# deps: fastapi, pydantic, sqlalchemy
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter(prefix="/api", tags=["advisory_freshness_probe"])


class FeedFreshnessStats(BaseModel):
    name: str
    count: int
    avg_age_hours: float
    max_age_hours: float
    min_age_hours: float


class OverallFreshnessStats(BaseModel):
    total_count: int
    fresh_count: int
    stale_count: int
    oldest_published_hours: float
    average_age_hours: float


class AdvisoryFreshnessResponse(BaseModel):
    overall: OverallFreshnessStats
    by_feed: list[FeedFreshnessStats]


def _compute_freshness(db: Session) -> tuple[OverallFreshnessStats, list[FeedFreshnessStats]]:
    now = datetime.now(timezone.utc)

    rows = db.query(
        VulnAdvisory.feed,
        VulnAdvisory.published_at,
    ).all()

    if not rows:
        empty_overall = OverallFreshnessStats(
            total_count=0,
            fresh_count=0,
            stale_count=0,
            oldest_published_hours=0.0,
            average_age_hours=0.0,
        )
        return empty_overall, []

    feed_data: dict[str, list[float]] = {}
    total_ages: list[float] = []
    fresh_count = 0
    stale_count = 0

    for row in rows:
        if row.published_at is None:
            continue
        if row.published_at.tzinfo is None:
            row_published = row.published_at.replace(tzinfo=timezone.utc)
        else:
            row_published = row.published_at
        age_hours = (now - row_published).total_seconds() / 3600
        total_ages.append(age_hours)

        if age_hours <= 168:
            fresh_count += 1
        else:
            stale_count += 1

        if row.feed not in feed_data:
            feed_data[row.feed] = []
        feed_data[row.feed].append(age_hours)

    oldest = max(total_ages) if total_ages else 0.0
    avg_age = sum(total_ages) / len(total_ages) if total_ages else 0.0

    overall = OverallFreshnessStats(
        total_count=len(rows),
        fresh_count=fresh_count,
        stale_count=stale_count,
        oldest_published_hours=round(oldest, 2),
        average_age_hours=round(avg_age, 2),
    )

    by_feed = [
        FeedFreshnessStats(
            name=feed,
            count=len(ages),
            avg_age_hours=round(sum(ages) / len(ages), 2),
            max_age_hours=round(max(ages), 2),
            min_age_hours=round(min(ages), 2),
        )
        for feed, ages in feed_data.items()
    ]

    return overall, by_feed


@router.get("/advisory-freshness-probe/stats", response_model=AdvisoryFreshnessResponse)
def get_freshness_stats(
    feed: Optional[str] = Query(None),
    db: Session = Depends(get_session),
) -> AdvisoryFreshnessResponse:
    overall, by_feed = _compute_freshness(db)

    if feed is not None:
        by_feed = [f for f in by_feed if f.name == feed]

    return AdvisoryFreshnessResponse(overall=overall, by_feed=by_feed)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.main import app
    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    session = TestSession()
    now = datetime.now(timezone.utc)

    advisories = [
        VulnAdvisory(
            id="CVE-2026-0001",
            feed="nvd",
            source_url="https://nvd.nist.gov/vuln/detail/CVE-2026-0001",
            published_at=now - timedelta(hours=5),
            fetched_at=now - timedelta(hours=1),
            summary="Test advisory 1",
            severity="high",
        ),
        VulnAdvisory(
            id="CVE-2026-0002",
            feed="nvd",
            source_url="https://nvd.nist.gov/vuln/detail/CVE-2026-0002",
            published_at=now - timedelta(hours=20),
            fetched_at=now - timedelta(hours=2),
            summary="Test advisory 2",
            severity="critical",
        ),
        VulnAdvisory(
            id="GHSA-2026-abcd-1234",
            feed="ghsa",
            source_url="https://github.com/advisories/GHSA-2026-abcd-1234",
            published_at=now - timedelta(hours=200),
            fetched_at=now - timedelta(hours=3),
            summary="Test advisory 3",
            severity="medium",
        ),
    ]
    session.add_all(advisories)
    session.commit()

    overall, by_feed = _compute_freshness(session)

    assert overall.total_count == 3, f"Expected 3, got {overall.total_count}"
    assert overall.fresh_count == 2, f"Expected 2 fresh, got {overall.fresh_count}"
    assert overall.stale_count == 1, f"Expected 1 stale, got {overall.stale_count}"
    assert len(by_feed) == 2, f"Expected 2 feeds, got {len(by_feed)}"

    nvd = next(f for f in by_feed if f.name == "nvd")
    assert nvd.count == 2, f"Expected 2 in nvd, got {nvd.count}"

    ghsa = next(f for f in by_feed if f.name == "ghsa")
    assert ghsa.count == 1, f"Expected 1 in ghsa, got {ghsa.count}"

    app.dependency_overrides.clear()
    print("PASS")
