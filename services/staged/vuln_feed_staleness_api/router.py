from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import VulnAdvisory, VulnLink
from .logic import *  # noqa: F401,F403


router = APIRouter()


class SeverityCounts(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0


class FeedStaleness(BaseModel):
    feed: str
    advisories: int
    last_published_at: Optional[str] = None
    last_fetched_at: Optional[str] = None
    staleness_days: Optional[float] = None
    stale: bool
    severity_counts: SeverityCounts
    linked_servers: int


class FeedStalenessResponse(BaseModel):
    feeds: List[FeedStaleness]
    total_feeds: int
    stale_feeds: int


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: Any) -> Optional[str]:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


@router.get(
    "/vuln/feeds",
    response_model=FeedStalenessResponse,
    name="get_vuln_feed_staleness",
)
def get_vuln_feed_staleness(
    feed: Optional[str] = Query(default=None),
    stale_after_days: int = Query(default=7),
    session: Any = Depends(get_session),
) -> FeedStalenessResponse:
    stale_after_days = max(1, min(365, stale_after_days))
    advisories = session.query(VulnAdvisory).all()
    links = session.query(VulnLink).all()

    grouped: Dict[str, Dict[str, Any]] = {}
    advisory_ids_by_feed: Dict[str, set] = {}

    for advisory in advisories:
        advisory_feed = advisory.feed
        if feed is not None and advisory_feed != feed:
            continue

        group = grouped.setdefault(
            advisory_feed,
            {
                "advisories": 0,
                "last_published_at": None,
                "last_fetched_at": None,
                "severity_counts": {
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "unknown": 0,
                },
            },
        )
        advisory_ids_by_feed.setdefault(advisory_feed, set()).add(advisory.id)
        group["advisories"] += 1

        published_at = _as_utc(advisory.published_at)
        fetched_at = _as_utc(advisory.fetched_at)
        if published_at is not None and (
            group["last_published_at"] is None
            or published_at > group["last_published_at"]
        ):
            group["last_published_at"] = published_at
        if fetched_at is not None and (
            group["last_fetched_at"] is None
            or fetched_at > group["last_fetched_at"]
        ):
            group["last_fetched_at"] = fetched_at

        severity = (advisory.severity or "unknown").strip().lower()
        if severity not in group["severity_counts"]:
            severity = "unknown"
        group["severity_counts"][severity] += 1

    if feed is not None and feed not in grouped:
        grouped[feed] = {
            "advisories": 0,
            "last_published_at": None,
            "last_fetched_at": None,
            "severity_counts": {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "unknown": 0,
            },
        }

    servers_by_feed: Dict[str, set] = {}
    for link in links:
        for advisory_feed, advisory_ids in advisory_ids_by_feed.items():
            if link.advisory_id in advisory_ids:
                servers_by_feed.setdefault(advisory_feed, set()).add(link.server_id)

    now = datetime.now(timezone.utc)
    reports: List[FeedStaleness] = []

    for advisory_feed in sorted(grouped):
        group = grouped[advisory_feed]
        last_fetched_at = group["last_fetched_at"]
        staleness_days = (
            max(0.0, (now - last_fetched_at).total_seconds() / 86400.0)
            if last_fetched_at is not None
            else None
        )
        stale = (
            staleness_days is not None and staleness_days >= stale_after_days
        )

        reports.append(
            FeedStaleness(
                feed=advisory_feed,
                advisories=group["advisories"],
                last_published_at=_timestamp(group["last_published_at"]),
                last_fetched_at=_timestamp(last_fetched_at),
                staleness_days=staleness_days,
                stale=stale,
                severity_counts=SeverityCounts(**group["severity_counts"]),
                linked_servers=len(servers_by_feed.get(advisory_feed, set())),
            )
        )

    return FeedStalenessResponse(
        feeds=reports,
        total_feeds=len(reports),
        stale_feeds=sum(1 for report in reports if report.stale),
    )


if __name__ == "__main__":
    from datetime import timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class FakeQuery:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class FakeSession:
        def __init__(self):
            now = datetime.now(timezone.utc)
            self.advisories = [
                VulnAdvisory(
                    id=1,
                    feed="osv",
                    summary="Test advisory",
                    severity="high",
                    ecosystem="pypi",
                    package="example",
                    published_at=now - timedelta(days=3),
                    fetched_at=now - timedelta(days=2),
                    source_url="https://example.invalid/advisory/1",
                    content_hash="hash-1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="github",
                    summary="Test advisory",
                    severity="critical",
                    ecosystem="npm",
                    package="example",
                    published_at=now - timedelta(days=4),
                    fetched_at=now - timedelta(days=1),
                    source_url="https://example.invalid/advisory/2",
                    content_hash="hash-2",
                ),
            ]
            self.links = [VulnLink(advisory_id=1, server_id=10)]

        def query(self, model):
            if model is VulnAdvisory:
                return FakeQuery(self.advisories)
            if model is VulnLink:
                return FakeQuery(self.links)
            raise AssertionError(f"Unexpected model: {model}")

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = lambda: FakeSession()

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/feeds")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_feeds"] == 2
        osv = next(item for item in payload["feeds"] if item["feed"] == "osv")
        assert osv["linked_servers"] == 1
        assert osv["staleness_days"] is not None
        assert set(osv["severity_counts"]) == {
            "critical",
            "high",
            "medium",
            "low",
            "unknown",
        }

    print("PASS")