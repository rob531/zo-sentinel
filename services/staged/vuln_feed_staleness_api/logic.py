from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(
    prefix="/api",
    tags=["vuln_feed_staleness_api"],
)


class FeedFreshness(BaseModel):
    feed: str
    advisories: int
    last_published_at: Optional[str]
    last_fetched_at: Optional[str]
    staleness_days: Optional[float]
    stale: bool
    severity_counts: dict[str, int]
    linked_servers: int


class VulnFeedFreshnessResponse(BaseModel):
    feeds: list[FeedFreshness]
    total_feeds: int
    stale_feeds: int


def _result_rows(result: Any) -> list[Any]:
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        return list(scalars().all())
    return list(result)


def _timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _staleness_days(value: Any, now: datetime) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (now - value.astimezone(timezone.utc)).total_seconds() / 86400.0


@router.get(
    "/vuln/feeds",
    response_model=VulnFeedFreshnessResponse,
    name="vuln_feed_staleness_api_feeds",
)
def get_vuln_feeds(
    session: Session = Depends(get_session),
    feed: Optional[str] = Query(default=None),
    stale_after_days: int = Query(default=7),
) -> VulnFeedFreshnessResponse:
    threshold = max(1, min(365, int(stale_after_days)))
    now = datetime.now(timezone.utc)

    advisory_query = select(VulnAdvisory)
    if feed is not None:
        advisory_query = advisory_query.where(VulnAdvisory.feed == feed)

    advisories = _result_rows(session.execute(advisory_query))

    grouped: dict[str, list[VulnAdvisory]] = {}
    for advisory in advisories:
        advisory_feed = advisory.feed
        if advisory_feed is None:
            advisory_feed = ""
        grouped.setdefault(str(advisory_feed), []).append(advisory)

    advisory_ids = [
        advisory.id
        for rows in grouped.values()
        for advisory in rows
        if advisory.id is not None
    ]
    links: list[VulnLink] = []
    if advisory_ids:
        link_query = select(VulnLink).where(VulnLink.advisory_id.in_(advisory_ids))
        links = _result_rows(session.execute(link_query))

    links_by_advisory: dict[Any, list[VulnLink]] = {}
    for link in links:
        links_by_advisory.setdefault(link.advisory_id, []).append(link)

    feeds: list[FeedFreshness] = []
    for advisory_feed in sorted(grouped):
        rows = grouped[advisory_feed]
        severity_counts = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "unknown": 0,
        }

        for advisory in rows:
            severity = advisory.severity
            if not isinstance(severity, str):
                severity = ""
            severity = severity.strip().lower()
            if severity in severity_counts:
                severity_counts[severity] += 1
            else:
                severity_counts["unknown"] += 1

        published_values = [
            advisory.published_at
            for advisory in rows
            if advisory.published_at is not None
        ]
        fetched_values = [
            advisory.fetched_at
            for advisory in rows
            if advisory.fetched_at is not None
        ]

        last_published_at = max(published_values) if published_values else None
        last_fetched_at = max(fetched_values) if fetched_values else None
        staleness = _staleness_days(last_fetched_at, now)

        linked_server_ids: set[Any] = set()
        for advisory in rows:
            for link in links_by_advisory.get(advisory.id, []):
                if link.server_id is not None:
                    linked_server_ids.add(link.server_id)

        feeds.append(
            FeedFreshness(
                feed=advisory_feed,
                advisories=len(rows),
                last_published_at=_timestamp(last_published_at),
                last_fetched_at=_timestamp(last_fetched_at),
                staleness_days=staleness,
                stale=staleness is not None and staleness > threshold,
                severity_counts=severity_counts,
                linked_servers=len(linked_server_ids),
            )
        )

    if feed is not None and feed not in grouped:
        feeds.append(
            FeedFreshness(
                feed=feed,
                advisories=0,
                last_published_at=None,
                last_fetched_at=None,
                staleness_days=None,
                stale=False,
                severity_counts={
                    "critical": 0,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "unknown": 0,
                },
                linked_servers=0,
            )
        )

    return VulnFeedFreshnessResponse(
        feeds=feeds,
        total_feeds=len(feeds),
        stale_feeds=sum(1 for item in feeds if item.stale),
    )


get_feeds = get_vuln_feeds


if __name__ == "__main__":
    from types import SimpleNamespace

    from fastapi import FastAPI

    class _FakeResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> "_FakeResult":
            return self

        def all(self) -> list[Any]:
            return self._rows

    class _FakeSession:
        def __init__(self) -> None:
            now = datetime.now(timezone.utc)
            self.advisories = [
                SimpleNamespace(
                    id=1,
                    feed="nvd",
                    summary="test advisory",
                    severity="critical",
                    ecosystem="pypi",
                    package="example",
                    published_at=now,
                    fetched_at=now - timezone.utc.utcoffset(now),
                    source_url="https://example.invalid/advisory/1",
                    content_hash="hash-1",
                ),
                SimpleNamespace(
                    id=2,
                    feed="osv",
                    summary="test advisory",
                    severity="not-a-severity",
                    ecosystem="npm",
                    package="example",
                    published_at=now,
                    fetched_at=now - timezone.utc.utcoffset(now),
                    source_url="https://example.invalid/advisory/2",
                    content_hash="hash-2",
                ),
            ]
            self.links = [
                SimpleNamespace(
                    id=1,
                    advisory_id=1,
                    server_id="server-1",
                    linked_at=now,
                    match_basis="package",
                    match_confidence=1.0,
                    match_value="example",
                )
            ]

        def execute(self, statement: Any) -> _FakeResult:
            entity = statement.column_descriptions[0].get("entity")
            if entity is VulnAdvisory:
                return _FakeResult(self.advisories)
            if entity is VulnLink:
                return _FakeResult(self.links)
            return _FakeResult([])

    fake_session = _FakeSession()
    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = lambda: fake_session
    that_app.include_router(router)

    result = get_vuln_feeds(
        session=fake_session,
        feed=None,
        stale_after_days=7,
    )
    assert result.total_feeds == 2
    assert result.feeds[0].linked_servers == 1
    assert result.feeds[0].staleness_days is not None
    assert set(result.feeds[0].severity_counts) == {
        "critical",
        "high",
        "medium",
        "low",
        "unknown",
    }
    assert result.feeds[1].severity_counts["unknown"] == 1
    print("PASS")