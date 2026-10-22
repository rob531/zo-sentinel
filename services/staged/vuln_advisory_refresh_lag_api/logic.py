from datetime import datetime, timezone
from statistics import median
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter()


class FeedLag(BaseModel):
    feed: str
    advisories: int
    linked_servers: int
    median_lag_hours: float
    max_lag_hours: float
    stale: int


class AdvisoryRefreshLagResponse(BaseModel):
    stale_hours: int
    feeds: List[FeedLag]
    total_feeds: int
    computed_at: str


def _utc_datetime(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get(
    "/vuln/advisories/lag",
    response_model=AdvisoryRefreshLagResponse,
)
def get_advisory_refresh_lag(
    feed: Optional[str] = Query(default=None),
    stale_hours: int = Query(default=48, ge=0),
    session: Session = Depends(get_session),
) -> AdvisoryRefreshLagResponse:
    advisory_query = select(VulnAdvisory)
    link_query = select(VulnLink.advisory_id, VulnLink.server_id).join(
        VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id
    )

    if feed is not None:
        advisory_query = advisory_query.where(VulnAdvisory.feed == feed)
        link_query = link_query.where(VulnAdvisory.feed == feed)

    advisories = list(session.execute(advisory_query).scalars())
    links = list(session.execute(link_query))

    advisory_feed = {
        advisory.id: advisory.feed
        for advisory in advisories
        if advisory.id is not None
    }
    linked_servers = {}
    for advisory_id, server_id in links:
        feed_name = advisory_feed.get(advisory_id)
        if feed_name is None:
            continue
        linked_servers.setdefault(feed_name, set()).add(server_id)

    grouped = {}
    for advisory in advisories:
        grouped.setdefault(advisory.feed, []).append(advisory)

    feed_results = []
    for feed_name, feed_advisories in grouped.items():
        lag_hours = []
        stale = 0

        for advisory in feed_advisories:
            if advisory.published_at is None or advisory.fetched_at is None:
                continue
            lag = (
                _utc_datetime(advisory.fetched_at)
                - _utc_datetime(advisory.published_at)
            ).total_seconds() / 3600.0
            lag_hours.append(lag)
            if lag > stale_hours:
                stale += 1

        feed_results.append(
            FeedLag(
                feed=feed_name,
                advisories=len(feed_advisories),
                linked_servers=len(linked_servers.get(feed_name, set())),
                median_lag_hours=float(median(lag_hours)) if lag_hours else 0.0,
                max_lag_hours=float(max(lag_hours)) if lag_hours else 0.0,
                stale=stale,
            )
        )

    feed_results.sort(key=lambda item: (-item.advisories, item.feed))

    return AdvisoryRefreshLagResponse(
        stale_hours=stale_hours,
        feeds=feed_results,
        total_feeds=len(feed_results),
        computed_at=datetime.now(timezone.utc).isoformat(),
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[VulnAdvisory.__table__, VulnLink.__table__],
    )
    test_session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old_published = now - timedelta(days=12)
    old_fetched = now - timedelta(days=2)

    with test_session_factory() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=101,
                    feed="osv",
                    severity="HIGH",
                    ecosystem="PyPI",
                    package="old-package",
                    published_at=old_published,
                    fetched_at=old_fetched,
                    source_url="https://example.invalid/osv/101",
                    summary="Old advisory",
                    aliases=[],
                    identities=[],
                    affected_ranges=[],
                    content_hash="osv-101",
                ),
                VulnAdvisory(
                    id=102,
                    feed="osv",
                    severity="MEDIUM",
                    ecosystem="npm",
                    package="current-package",
                    published_at=now - timedelta(days=2, hours=1),
                    fetched_at=now - timedelta(days=2),
                    source_url="https://example.invalid/osv/102",
                    summary="Current advisory",
                    aliases=[],
                    identities=[],
                    affected_ranges=[],
                    content_hash="osv-102",
                ),
                VulnAdvisory(
                    id=201,
                    feed="github",
                    severity="LOW",
                    ecosystem="Go",
                    package="current-package",
                    published_at=now - timedelta(hours=2),
                    fetched_at=now - timedelta(hours=1),
                    source_url="https://example.invalid/github/201",
                    summary="GitHub advisory",
                    aliases=[],
                    identities=[],
                    affected_ranges=[],
                    content_hash="github-201",
                ),
                VulnLink(
                    id=301,
                    advisory_id=101,
                    server_id="server-1",
                    linked_at=now,
                    match_basis="package",
                    match_confidence=1.0,
                    match_value="old-package",
                ),
                VulnLink(
                    id=302,
                    advisory_id=101,
                    server_id="server-2",
                    linked_at=now,
                    match_basis="package",
                    match_confidence=1.0,
                    match_value="old-package",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router, prefix="/api")

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisories/lag", params={"stale_hours": 48})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["feeds"]) == 2
        assert payload["feeds"][0]["feed"] == "osv"
        assert payload["feeds"][0]["advisories"] == 2
        assert payload["feeds"][0]["stale"] == 1
        assert payload["feeds"][0]["linked_servers"] == 2
        assert payload["feeds"][0]["max_lag_hours"] > 0
        assert payload["total_feeds"] == 2
        datetime.fromisoformat(payload["computed_at"].replace("Z", "+00:00"))

    print("PASS")