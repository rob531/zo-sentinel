# services/staged/advisory_freshness_api/contract.py
from __future__ import annotations

import datetime as _dt
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field

from app.db import Base, get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api")


class FeedInfo(BaseModel):
    feed: str
    advisory_count: int
    linked_count: int
    newest_published_at: _dt.datetime
    newest_fetched_at: _dt.datetime
    age_hours: float


class FreshnessResponse(BaseModel):
    generated_at: _dt.datetime = Field(default_factory=_dt.datetime.utcnow)
    feeds: List[FeedInfo]
    orphan_feeds: List[str]


@router.get(
    "/advisories/freshness",
    response_model=FreshnessResponse,
    name="advisory_freshness",
)
def get_advisory_freshness(session=Depends(get_session)):
    """Return freshness statistics for vulnerability advisories."""
    now = _dt.datetime.utcnow()

    # Gather all advisories
    advisories = session.query(VulnAdvisory).all()

    # Map feed -> list of advisories
    feed_map: dict[str, List[VulnAdvisory]] = {}
    for adv in advisories:
        feed_map.setdefault(adv.feed, []).append(adv)

    # Advisory IDs that have at least one link
    linked_adv_ids = {
        link.advisory_id for link in session.query(VulnLink.advisory_id).distinct()
    }

    feeds_info: List[FeedInfo] = []
    orphan_feeds: List[str] = []

    for feed, advs in feed_map.items():
        advisory_count = len(advs)
        linked_count = sum(1 for a in advs if a.id in linked_adv_ids)

        newest_published = max(a.published_at for a in advs)
        newest_fetched = max(a.fetched_at for a in advs)

        age_hours = (now - newest_published).total_seconds() / 3600.0

        feeds_info.append(
            FeedInfo(
                feed=feed,
                advisory_count=advisory_count,
                linked_count=linked_count,
                newest_published_at=newest_published,
                newest_fetched_at=newest_fetched,
                age_hours=age_hours,
            )
        )

        if linked_count == 0:
            orphan_feeds.append(feed)

    return FreshnessResponse(generated_at=now, feeds=feeds_info, orphan_feeds=orphan_feeds)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.advisory_freshness_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mimics the real models
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------- #
    # Populate test data
    # ------------------------------------------------------------------- #
    test_session = TestingSessionLocal()
    now = _dt.datetime.utcnow()

    adv1 = VulnAdvisory(
        id=1,
        feed="testfeed",
        package="pkg1",
        ecosystem="pypi",
        published_at=now - _dt.timedelta(hours=5),
        fetched_at=now - _dt.timedelta(hours=4, minutes=30),
        affected_ranges="*",
        aliases="",
        content_hash="hash1",
        identities="",
        severity="high",
        source_url="http://example.com/1",
        summary="adv 1",
    )
    adv2 = VulnAdvisory(
        id=2,
        feed="testfeed",
        package="pkg2",
        ecosystem="pypi",
        published_at=now - _dt.timedelta(hours=2),
        fetched_at=now - _dt.timedelta(hours=1, minutes=45),
        affected_ranges="*",
        aliases="",
        content_hash="hash2",
        identities="",
        severity="medium",
        source_url="http://example.com/2",
        summary="adv 2",
    )
    test_session.add_all([adv1, adv2])
    test_session.flush()

    link = VulnLink(
        id=1,
        advisory_id=adv1.id,
        server_id=1,
        linked_at=now - _dt.timedelta(hours=3),
        match_basis="test",
        match_confidence=1.0,
        match_value="value",
    )
    test_session.add(link)
    test_session.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_test_session():
        return TestingSessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute request and validate response
    # ------------------------------------------------------------------- #
    resp = client.get("/api/advisories/freshness")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()
    assert "feeds" in data and isinstance(data["feeds"], list)
    feed0 = data["feeds"][0]
    assert feed0["advisory_count"] == 2, "advisory count mismatch"
    assert feed0["linked_count"] == 1, "linked count mismatch"

    print("PASS")