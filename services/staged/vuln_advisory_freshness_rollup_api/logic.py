from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


router = APIRouter(prefix="/api")


def _timestamp_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, time.min).isoformat()
    return str(value)


def _timestamp_sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (1, "")
    if isinstance(value, datetime):
        return (0, value.timestamp())
    if isinstance(value, date):
        return (0, datetime.combine(value, time.min).timestamp())
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (0, parsed.timestamp())
    except ValueError:
        return (1, text)


def _is_stale(value: Any, now: datetime | None = None) -> bool:
    if value is None:
        return False
    current = now or datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed < current - timedelta(hours=24)


def get_freshness_rollup(
    feed: str | None = None,
    ecosystem: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    filters = []
    if feed:
        filters.append(VulnAdvisory.feed == feed)
    if ecosystem:
        filters.append(VulnAdvisory.ecosystem == ecosystem)

    advisories = list(session.execute(select(VulnAdvisory).where(*filters)).scalars().all())

    advisory_ids = [advisory.id for advisory in advisories if advisory.id is not None]
    link_rows = []
    if advisory_ids:
        link_rows = list(
            session.execute(
                select(VulnLink.advisory_id, VulnLink.id).where(
                    VulnLink.advisory_id.in_(advisory_ids)
                )
            ).all()
        )

    links_by_advisory: dict[Any, int] = {}
    total_links = 0
    for advisory_id, _link_id in link_rows:
        total_links += 1
        links_by_advisory[advisory_id] = links_by_advisory.get(advisory_id, 0) + 1

    grouped: dict[str, list[VulnAdvisory]] = {}
    for advisory in advisories:
        grouped.setdefault(str(advisory.feed), []).append(advisory)

    now = datetime.now(timezone.utc)
    feeds = []
    for feed_name in sorted(grouped):
        feed_advisories = grouped[feed_name]
        severity_counts: dict[str, int] = {}
        for advisory in feed_advisories:
            if advisory.severity is not None:
                severity = str(advisory.severity)
                severity_counts[severity] = severity_counts.get(severity, 0) + 1

        published_values = [
            advisory.published_at
            for advisory in feed_advisories
            if advisory.published_at is not None
        ]
        fetched_values = [
            advisory.fetched_at
            for advisory in feed_advisories
            if advisory.fetched_at is not None
        ]

        oldest_published = (
            min(published_values, key=_timestamp_sort_key)
            if published_values
            else None
        )
        newest_published = (
            max(published_values, key=_timestamp_sort_key)
            if published_values
            else None
        )
        oldest_fetched = (
            min(fetched_values, key=_timestamp_sort_key)
            if fetched_values
            else None
        )

        feeds.append(
            {
                "feed": feed_name,
                "total": len(feed_advisories),
                "by_severity": dict(sorted(severity_counts.items())),
                "linked_count": sum(
                    1
                    for advisory in feed_advisories
                    if links_by_advisory.get(advisory.id, 0) > 0
                ),
                "oldest_published_at": _timestamp_value(oldest_published),
                "newest_published_at": _timestamp_value(newest_published),
                "oldest_fetched_at": _timestamp_value(oldest_fetched),
                "stale_over_24h": sum(
                    1
                    for advisory in feed_advisories
                    if _is_stale(advisory.fetched_at, now)
                ),
            }
        )

    return {
        "feeds": feeds,
        "total_advisories": len(advisories),
        "total_links": total_links,
    }


def get_freshness(
    feed: str | None = Query(default=None),
    ecosystem: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_freshness_rollup(feed=feed, ecosystem=ecosystem, session=session)


router.add_api_route("/vuln/freshness", get_freshness, methods=["GET"])


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
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
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=25)
        advisory_rows = [
            VulnAdvisory(
                id=1,
                feed="alpha",
                summary="Advisory 1",
                severity="critical",
                ecosystem="npm",
                package="pkg-one",
                affected_ranges="<1.0.0",
                aliases="CVE-1",
                source_url="https://example.test/1",
                published_at=now - timedelta(days=2),
                fetched_at=old,
            ),
            VulnAdvisory(
                id=2,
                feed="alpha",
                summary="Advisory 2",
                severity="medium",
                ecosystem="npm",
                package="pkg-two",
                affected_ranges="<2.0.0",
                aliases="CVE-2",
                source_url="https://example.test/2",
                published_at=now - timedelta(days=1),
                fetched_at=now,
            ),
            VulnAdvisory(
                id=3,
                feed="beta",
                summary="Advisory 3",
                severity="high",
                ecosystem="pypi",
                package="pkg-three",
                affected_ranges="<3.0.0",
                aliases="CVE-3",
                source_url="https://example.test/3",
                published_at=now - timedelta(hours=12),
                fetched_at=now,
            ),
            VulnAdvisory(
                id=4,
                feed="beta",
                summary="Advisory 4",
                severity="high",
                ecosystem="pypi",
                package="pkg-four",
                affected_ranges="<4.0.0",
                aliases="CVE-4",
                source_url="https://example.test/4",
                published_at=now - timedelta(hours=6),
                fetched_at=now,
            ),
        ]
        db.add_all(advisory_rows)
        db.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id=101,
                    match_basis="package",
                    match_confidence=0.9,
                    match_value="pkg-one",
                    linked_at=now,
                ),
                VulnLink(
                    id=2,
                    advisory_id=3,
                    server_id=102,
                    match_basis="package",
                    match_confidence=0.8,
                    match_value="pkg-three",
                    linked_at=now,
                ),
            ]
        )
        db.commit()

    def override_session():
        with TestSession() as db:
            yield db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get("/api/vuln/freshness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_advisories"] == 4
    alpha = next(item for item in payload["feeds"] if item["feed"] == "alpha")
    assert alpha["by_severity"] == {"critical": 1, "medium": 1}
    assert alpha["stale_over_24h"] == 1
    print("PASS")