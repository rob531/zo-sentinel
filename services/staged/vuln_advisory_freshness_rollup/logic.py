from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


class FreshnessResponse(BaseModel):
    by_feed: List[Dict[str, Any]]
    by_severity: Dict[str, int]
    by_ecosystem: Dict[str, int]
    unlinked: int
    totals: Dict[str, int]


router = APIRouter(prefix="/api")


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def build_freshness_rollup(
    session: Session,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    advisory_rows = session.execute(
        select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.published_at,
        )
    ).mappings().all()

    link_rows = session.execute(
        select(VulnLink.advisory_id, VulnLink.server_id)
    ).mappings().all()

    current_time = _coerce_datetime(now) or datetime.now(timezone.utc)
    stale_cutoff = current_time - timedelta(days=90)

    linked_advisory_ids = {
        row["advisory_id"]
        for row in link_rows
        if row["advisory_id"] is not None
    }

    feeds: Dict[str, Dict[str, Any]] = {}
    severity_counts: Dict[str, int] = {}
    ecosystem_counts: Dict[str, int] = {}

    for advisory in advisory_rows:
        feed = advisory["feed"] if advisory["feed"] is not None else "unknown"
        severity = (
            advisory["severity"] if advisory["severity"] is not None else "unknown"
        )
        ecosystem = (
            advisory["ecosystem"] if advisory["ecosystem"] is not None else "unknown"
        )

        feed_entry = feeds.setdefault(
            feed,
            {
                "feed": feed,
                "advisory_count": 0,
                "linked_count": 0,
                "oldest_published_at": None,
                "latest_published_at": None,
                "stale_count": 0,
            },
        )
        feed_entry["advisory_count"] += 1

        if advisory["id"] in linked_advisory_ids:
            feed_entry["linked_count"] += 1

        published_at = advisory["published_at"]
        published_datetime = _coerce_datetime(published_at)
        if published_datetime is not None:
            oldest = _coerce_datetime(feed_entry["oldest_published_at"])
            latest = _coerce_datetime(feed_entry["latest_published_at"])
            if oldest is None or published_datetime < oldest:
                feed_entry["oldest_published_at"] = published_at
            if latest is None or published_datetime > latest:
                feed_entry["latest_published_at"] = published_at
            if published_datetime < stale_cutoff:
                feed_entry["stale_count"] += 1

        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        ecosystem_counts[ecosystem] = ecosystem_counts.get(ecosystem, 0) + 1

    by_feed: List[Dict[str, Any]] = []
    for feed in sorted(feeds):
        entry = feeds[feed]
        entry["oldest_published_at"] = _iso_timestamp(entry["oldest_published_at"])
        entry["latest_published_at"] = _iso_timestamp(entry["latest_published_at"])
        by_feed.append(entry)

    distinct_servers = {
        row["server_id"]
        for row in link_rows
        if row["server_id"] is not None
    }
    unlinked = sum(
        1
        for advisory in advisory_rows
        if advisory["id"] not in linked_advisory_ids
    )

    return {
        "by_feed": by_feed,
        "by_severity": dict(sorted(severity_counts.items())),
        "by_ecosystem": dict(sorted(ecosystem_counts.items())),
        "unlinked": unlinked,
        "totals": {
            "advisories": len(advisory_rows),
            "links": len(link_rows),
            "distinct_servers": len(distinct_servers),
        },
    }


@router.get("/vuln/advisories/freshness", response_model=FreshnessResponse)
def get_freshness_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return build_freshness_rollup(session)


app = FastAPI(title="Vulnerability Advisory Freshness Rollup")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="feed-a",
                    severity="high",
                    ecosystem="PyPI",
                    package="package-a",
                    published_at=now - timedelta(days=120),
                    fetched_at=now,
                    content_hash="hash-a",
                    source_url="https://example.invalid/a",
                ),
                VulnAdvisory(
                    id=2,
                    feed="feed-a",
                    severity="medium",
                    ecosystem="PyPI",
                    package="package-b",
                    published_at=now - timedelta(days=10),
                    fetched_at=now,
                    content_hash="hash-b",
                    source_url="https://example.invalid/b",
                ),
                VulnAdvisory(
                    id=3,
                    feed="feed-b",
                    severity="low",
                    ecosystem=None,
                    package="package-c",
                    published_at=now - timedelta(days=5),
                    fetched_at=now,
                    content_hash="hash-c",
                    source_url="https://example.invalid/c",
                ),
            ]
        )
        session.add(
            VulnLink(
                id=1,
                advisory_id=1,
                server_id=42,
                match_confidence=0.99,
                match_basis="package",
                match_value="package-a",
                linked_at=now,
            )
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        response = TestClient(app).get("/api/vuln/advisories/freshness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["advisories"] == 3
        assert payload["totals"]["links"] == 1
        assert len(payload["by_feed"]) == 2
        linked_feed = next(
            item for item in payload["by_feed"] if item["feed"] == "feed-a"
        )
        assert linked_feed["linked_count"] == 1
        assert payload["unlinked"] == 2
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()