import json
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, distinct
from sqlalchemy.orm import Session

# Real data layer imports
from app.db import get_session, Base
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api")


# ---------- Pydantic response models ----------
class SeriesItem(BaseModel):
    date: str
    published: int
    critical: int
    high: int
    medium: int
    low: int
    linked: int


class EcosystemItem(BaseModel):
    ecosystem: str
    count: int


class FeedItem(BaseModel):
    feed: str
    count: int
    last_published: Optional[str] = None


class Totals(BaseModel):
    advisories: int
    linked: int
    unlinked: int
    median_days_to_link: float


class PublishVelocityResponse(BaseModel):
    days: int
    series: List[SeriesItem]
    ecosystems: List[EcosystemItem]
    feeds: List[FeedItem]
    totals: Totals


# ---------- Helper functions ----------
def _median(values: List[int]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


# ---------- Endpoint ----------
@router.get(
    "/cve/advisories/publish-velocity",
    response_model=PublishVelocityResponse,
    name="cve_advisory_publish_velocity",
)
def get_publish_velocity(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
):
    # Clamp days (already enforced by Query)
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)

    # ------------------------------------------------------------------
    # Fetch advisories (including those without a published_at)
    # ------------------------------------------------------------------
    advisories = (
        db.query(VulnAdvisory)
        .filter(
            (VulnAdvisory.published_at == None)  # noqa: E711
            | (func.date(VulnAdvisory.published_at) >= start_date)
        )
        .all()
    )
    advisory_ids = [a.id for a in advisories]

    # ------------------------------------------------------------------
    # Linked advisory ids
    # ------------------------------------------------------------------
    linked_rows = (
        db.query(VulnLink.advisory_id, VulnLink.linked_at, VulnAdvisory.published_at)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(VulnLink.advisory_id.in_(advisory_ids))
        .all()
    )
    linked_ids = {row[0] for row in linked_rows}
    link_days: List[int] = []
    for adv_id, linked_at, published_at in linked_rows:
        if published_at and linked_at:
            delta = (linked_at.date() - published_at.date()).days
            link_days.append(delta)

    # ------------------------------------------------------------------
    # Prepare series skeleton
    # ------------------------------------------------------------------
    series_dict: Dict[date, SeriesItem] = {}
    for i in range(days):
        d = start_date + timedelta(days=i)
        series_dict[d] = SeriesItem(
            date=d.isoformat(),
            published=0,
            critical=0,
            high=0,
            medium=0,
            low=0,
            linked=0,
        )

    # ------------------------------------------------------------------
    # Populate series counts
    # ------------------------------------------------------------------
    for adv in advisories:
        if adv.published_at:
            adv_date = adv.published_at.date()
            if adv_date in series_dict:
                item = series_dict[adv_date]
                item.published += 1
                sev = (adv.severity or "").lower()
                if sev == "critical":
                    item.critical += 1
                elif sev == "high":
                    item.high += 1
                elif sev == "medium":
                    item.medium += 1
                elif sev == "low":
                    item.low += 1
                if adv.id in linked_ids:
                    item.linked += 1

    series = [series_dict[d] for d in sorted(series_dict)]

    # ------------------------------------------------------------------
    # Ecosystem aggregation
    # ------------------------------------------------------------------
    ecosystem_counts: Dict[str, int] = {}
    for adv in advisories:
        eco = adv.ecosystem or "unknown"
        ecosystem_counts[eco] = ecosystem_counts.get(eco, 0) + 1
    ecosystems = [
        EcosystemItem(ecosystem=k, count=v) for k, v in sorted(ecosystem_counts.items())
    ]

    # ------------------------------------------------------------------
    # Feed aggregation
    # ------------------------------------------------------------------
    feed_counts: Dict[str, int] = {}
    feed_last: Dict[str, date] = {}
    for adv in advisories:
        feed = adv.feed or "unknown"
        feed_counts[feed] = feed_counts.get(feed, 0) + 1
        if adv.published_at:
            adv_date = adv.published_at.date()
            if feed not in feed_last or adv_date > feed_last[feed]:
                feed_last[feed] = adv_date
    feeds = []
    for feed in sorted(feed_counts):
        feeds.append(
            FeedItem(
                feed=feed,
                count=feed_counts[feed],
                last_published=feed_last.get(feed).isoformat()
                if feed_last.get(feed)
                else None,
            )
        )

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    total_advisories = len(advisories)
    total_linked = len(linked_ids)
    total_unlinked = total_advisories - total_linked
    median_days = _median(link_days)

    totals = Totals(
        advisories=total_advisories,
        linked=total_linked,
        unlinked=total_unlinked,
        median_days_to_link=median_days,
    )

    return PublishVelocityResponse(
        days=days,
        series=series,
        ecosystems=ecosystems,
        feeds=feeds,
        totals=totals,
    )


# ---------- Self‑test ----------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed data
    now = datetime.utcnow()
    adv1 = VulnAdvisory(
        id=1,
        feed="feed1",
        severity="critical",
        ecosystem="ecosystem1",
        published_at=now - timedelta(days=6),
        summary="adv1",
        source_url="",
        fetched_at=now,
        identities="",
        content_hash="",
        package="pkg1",
        affected_ranges="",
        aliases="",
    )
    adv2 = VulnAdvisory(
        id=2,
        feed="feed1",
        severity="high",
        ecosystem="ecosystem1",
        published_at=now - timedelta(days=5),
        summary="adv2",
        source_url="",
        fetched_at=now,
        identities="",
        content_hash="",
        package="pkg2",
        affected_ranges="",
        aliases="",
    )
    adv3 = VulnAdvisory(
        id=3,
        feed="feed2",
        severity="medium",
        ecosystem="ecosystem2",
        published_at=None,
        summary="adv3",
        source_url="",
        fetched_at=now,
        identities="",
        content_hash="",
        package="pkg3",
        affected_ranges="",
        aliases="",
    )
    link1 = VulnLink(
        id=1,
        advisory_id=1,
        server_id=1,
        match_basis="test",
        match_value="test",
        match_confidence=1.0,
        linked_at=now - timedelta(days=6),
    )
    session = TestSessionLocal()
    session.add_all([adv1, adv2, adv3, link1])
    session.commit()
    session.close()

    # Build FastAPI app with overrides
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:
        return TestSessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/cve/advisories/publish-velocity?days=7")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    try:
        assert data["days"] == 7
        assert len(data["series"]) == 7
        # series[0] corresponds to the oldest day (today-6)
        assert data["series"][0]["published"] == 1
        assert data["totals"]["linked"] == 1
        assert data["totals"]["unlinked"] == 2
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)