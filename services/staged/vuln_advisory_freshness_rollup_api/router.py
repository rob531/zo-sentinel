from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

try:
    from .logic import get_vuln_advisory_freshness_rollup as _logic_rollup
except (ImportError, ModuleNotFoundError):
    _logic_rollup = None


class FeedFreshness(BaseModel):
    feed: str
    total: int
    by_severity: Dict[str, int]
    linked_count: int
    oldest_published_at: Optional[str]
    newest_published_at: Optional[str]
    oldest_fetched_at: Optional[str]
    stale_over_24h: int


class FreshnessRollupResponse(BaseModel):
    feeds: List[FeedFreshness]
    total_advisories: int
    total_links: int


router = APIRouter(prefix="/api", tags=["vulnerability-advisories"])


def _timestamp_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _timestamp_key(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_rollup(session, feed: Optional[str], ecosystem: Optional[str]):
    from sqlalchemy import func

    query = session.query(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.severity,
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
        func.count(VulnLink.id).label("link_count"),
    ).outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)

    if feed:
        query = query.filter(VulnAdvisory.feed == feed)
    if ecosystem:
        query = query.filter(VulnAdvisory.ecosystem == ecosystem)

    rows = (
        query.group_by(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
        )
        .all()
    )

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    grouped: Dict[str, Dict] = {}

    for advisory_id, advisory_feed, severity, published_at, fetched_at, link_count in rows:
        bucket = grouped.setdefault(
            advisory_feed,
            {
                "feed": advisory_feed,
                "total": 0,
                "by_severity": {},
                "linked_count": 0,
                "oldest_published_at": None,
                "newest_published_at": None,
                "oldest_fetched_at": None,
                "stale_over_24h": 0,
                "_oldest_published_key": None,
                "_newest_published_key": None,
                "_oldest_fetched_key": None,
            },
        )

        bucket["total"] += 1
        severity_key = str(severity) if severity is not None else "unknown"
        bucket["by_severity"][severity_key] = (
            bucket["by_severity"].get(severity_key, 0) + 1
        )
        if link_count:
            bucket["linked_count"] += 1

        published_key = _timestamp_key(published_at)
        if published_key is not None:
            if (
                bucket["_oldest_published_key"] is None
                or published_key < bucket["_oldest_published_key"]
            ):
                bucket["_oldest_published_key"] = published_key
                bucket["oldest_published_at"] = _timestamp_text(published_at)
            if (
                bucket["_newest_published_key"] is None
                or published_key > bucket["_newest_published_key"]
            ):
                bucket["_newest_published_key"] = published_key
                bucket["newest_published_at"] = _timestamp_text(published_at)

        fetched_key = _timestamp_key(fetched_at)
        if fetched_key is not None:
            if (
                bucket["_oldest_fetched_key"] is None
                or fetched_key < bucket["_oldest_fetched_key"]
            ):
                bucket["_oldest_fetched_key"] = fetched_key
                bucket["oldest_fetched_at"] = _timestamp_text(fetched_at)
            if fetched_key < cutoff:
                bucket["stale_over_24h"] += 1

    feeds = []
    for advisory_feed in sorted(grouped):
        bucket = grouped[advisory_feed]
        bucket.pop("_oldest_published_key", None)
        bucket.pop("_newest_published_key", None)
        bucket.pop("_oldest_fetched_key", None)
        feeds.append(bucket)

    return {
        "feeds": feeds,
        "total_advisories": sum(bucket["total"] for bucket in grouped.values()),
        "total_links": sum(row.link_count for row in rows),
    }


@router.get("/vuln/freshness", response_model=FreshnessRollupResponse)
def get_freshness(
    feed: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    session=Depends(get_session),
):
    builder = _logic_rollup if _logic_rollup is not None else _build_rollup
    return builder(session, feed, ecosystem)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    Session = sessionmaker(bind=engine)

    db = Session()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="alpha",
                summary="First advisory",
                severity="CRITICAL",
                ecosystem="pypi",
                package="example-one",
                source_url="https://example.test/1",
                published_at=now,
                fetched_at=now - timedelta(hours=25),
                content_hash="hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="alpha",
                summary="Second advisory",
                severity="HIGH",
                ecosystem="pypi",
                package="example-two",
                source_url="https://example.test/2",
                published_at=now,
                fetched_at=now,
                content_hash="hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="alpha",
                summary="Third advisory",
                severity="MEDIUM",
                ecosystem="npm",
                package="example-three",
                source_url="https://example.test/3",
                published_at=now,
                fetched_at=now,
                content_hash="hash-3",
            ),
            VulnAdvisory(
                id=4,
                feed="beta",
                summary="Fourth advisory",
                severity="HIGH",
                ecosystem="pypi",
                package="example-four",
                source_url="https://example.test/4",
                published_at=now,
                fetched_at=now,
                content_hash="hash-4",
            ),
            VulnLink(
                id=1,
                advisory_id=1,
                server_id=101,
                match_basis="package",
                match_confidence=0.95,
                match_value="example-one",
                linked_at=now,
            ),
            VulnLink(
                id=2,
                advisory_id=2,
                server_id=102,
                match_basis="package",
                match_confidence=0.90,
                match_value="example-two",
                linked_at=now,
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        return db

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/freshness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 4
        assert payload["total_links"] == 2
        alpha = next(item for item in payload["feeds"] if item["feed"] == "alpha")
        assert alpha["total"] == 3
        assert alpha["by_severity"] == {"CRITICAL": 1, "HIGH": 1, "MEDIUM": 1}
        assert alpha["linked_count"] == 2
        assert alpha["stale_over_24h"] == 1

    db.close()
    engine.dispose()
    print("PASS")