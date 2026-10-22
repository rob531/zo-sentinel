from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Generator, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


class FeedLag(BaseModel):
    feed: str
    advisories: int
    linked_servers: int
    median_lag_hours: float
    max_lag_hours: float
    stale: int


class AdvisoryLagResponse(BaseModel):
    stale_hours: int
    feeds: list[FeedLag]
    total_feeds: int
    computed_at: str


router = APIRouter(prefix="/api")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/vuln/advisories/lag", response_model=AdvisoryLagResponse)
def advisory_refresh_lag(
    feed: Optional[str] = Query(default=None),
    stale_hours: int = Query(default=48, ge=0),
    session: Session = Depends(get_session),
) -> AdvisoryLagResponse:
    computed_at = datetime.now(timezone.utc)
    stale_cutoff = computed_at - timedelta(hours=stale_hours)

    statement = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
    )
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)

    rows = session.execute(statement).all()
    advisory_ids = [row.id for row in rows]

    links_by_advisory: dict[object, set[object]] = {}
    if advisory_ids:
        link_rows = session.execute(
            select(VulnLink.advisory_id, VulnLink.server_id).where(
                VulnLink.advisory_id.in_(advisory_ids)
            )
        ).all()
        for advisory_id, server_id in link_rows:
            if server_id is not None:
                links_by_advisory.setdefault(advisory_id, set()).add(server_id)

    feeds: dict[str, dict[str, object]] = {}
    for advisory_id, feed_name, published_at, fetched_at in rows:
        entry = feeds.setdefault(
            feed_name,
            {
                "advisories": 0,
                "linked_servers": set(),
                "lags": [],
                "stale": 0,
            },
        )
        entry["advisories"] = int(entry["advisories"]) + 1
        entry["linked_servers"].update(links_by_advisory.get(advisory_id, set()))

        if published_at is not None and fetched_at is not None:
            lag_hours = (
                _as_utc(fetched_at) - _as_utc(published_at)
            ).total_seconds() / 3600.0
            entry["lags"].append(lag_hours)

        if fetched_at is not None and _as_utc(fetched_at) < stale_cutoff:
            entry["stale"] = int(entry["stale"]) + 1

    feed_results = [
        FeedLag(
            feed=feed_name,
            advisories=int(entry["advisories"]),
            linked_servers=len(entry["linked_servers"]),
            median_lag_hours=float(median(entry["lags"]))
            if entry["lags"]
            else 0.0,
            max_lag_hours=float(max(entry["lags"])) if entry["lags"] else 0.0,
            stale=int(entry["stale"]),
        )
        for feed_name, entry in feeds.items()
    ]
    feed_results.sort(key=lambda item: (-item.advisories, item.feed))

    return AdvisoryLagResponse(
        stale_hours=stale_hours,
        feeds=feed_results,
        total_feeds=len(feed_results),
        computed_at=computed_at.isoformat(),
    )


app = FastAPI(title="Vulnerability Advisory Refresh Lag API")
app.include_router(router)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed VARCHAR(255),
                    published_at DATETIME,
                    fetched_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id INTEGER
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    old_fetched_at = now - timedelta(days=2, seconds=1)
    rows = [
        {
            "id": 1,
            "feed": "osv",
            "published_at": old_fetched_at - timedelta(days=10),
            "fetched_at": old_fetched_at,
        },
        {
            "id": 2,
            "feed": "osv",
            "published_at": now - timedelta(days=3),
            "fetched_at": now - timedelta(days=1),
        },
        {
            "id": 3,
            "feed": "github",
            "published_at": now - timedelta(days=5),
            "fetched_at": now - timedelta(days=4),
        },
    ]
    with engine.begin() as connection:
        for row in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO vuln_advisories (id, feed, published_at, fetched_at)
                    VALUES (:id, :feed, :published_at, :fetched_at)
                    """
                ),
                row,
            )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links (id, advisory_id, server_id)
                VALUES (1, 1, 101), (2, 2, 102)
                """
            )
        )

    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/vuln/advisories/lag", params={"stale_hours": 48}
            )
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["feeds"]) == 2
            osv = next(item for item in payload["feeds"] if item["feed"] == "osv")
            assert osv["advisories"] == 2
            assert osv["stale"] == 1
            assert osv["max_lag_hours"] > 0
            datetime.fromisoformat(payload["computed_at"])

            filtered = client.get(
                "/api/vuln/advisories/lag",
                params={"feed": "osv", "stale_hours": 48},
            )
            assert filtered.status_code == 200
            filtered_payload = filtered.json()
            assert filtered_payload["total_feeds"] == 1
            assert filtered_payload["feeds"][0]["feed"] == "osv"
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()