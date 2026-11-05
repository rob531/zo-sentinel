"""Logic for advisory freshness reporting over the real application database."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


_SEVERITIES = ("critical", "high", "medium", "low")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def get_freshness_board(
    db: Session,
    feed: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return per-feed advisory counts, severity mixes, and freshness buckets."""
    reference_time = _as_utc(now or datetime.now(timezone.utc))
    statement = select(
        VulnAdvisory.feed,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
        VulnAdvisory.source_url,
    )
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in db.execute(statement).all():
        feed_name = row.feed or "unknown"
        group = grouped.setdefault(
            feed_name,
            {
                "advisory_count": 0,
                "severity_counts": {severity: 0 for severity in _SEVERITIES},
                "newest_published_at": None,
                "newest_fetched_at": None,
            },
        )
        group["advisory_count"] += 1

        severity = str(row.severity or "").strip().lower()
        if severity in group["severity_counts"]:
            group["severity_counts"][severity] += 1

        if row.published_at is not None:
            published_at = _as_utc(row.published_at)
            if (
                group["newest_published_at"] is None
                or published_at > group["newest_published_at"]
            ):
                group["newest_published_at"] = published_at

        if row.fetched_at is not None:
            fetched_at = _as_utc(row.fetched_at)
            if (
                group["newest_fetched_at"] is None
                or fetched_at > group["newest_fetched_at"]
            ):
                group["newest_fetched_at"] = fetched_at

    feeds = []
    for feed_name, group in grouped.items():
        fetched_at = group["newest_fetched_at"]
        if fetched_at is None:
            age_hours = 0.0
        else:
            age_hours = max(
                0.0,
                (reference_time - fetched_at).total_seconds() / 3600.0,
            )

        if age_hours <= 24:
            bucket = "fresh"
        elif age_hours <= 72:
            bucket = "aging"
        else:
            bucket = "stale"

        feeds.append(
            {
                "feed": feed_name,
                "advisory_count": group["advisory_count"],
                "newest_published_at": _isoformat(group["newest_published_at"]),
                "age_hours": round(age_hours, 3),
                "bucket": bucket,
                "severity_counts": group["severity_counts"],
            }
        )

    feeds.sort(key=lambda item: (-item["age_hours"], item["feed"]))
    return {"feeds": feeds}


get_freshness = get_freshness_board
get_advisory_freshness = get_freshness_board
get_freshness_data = get_freshness_board


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    try:
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        @compiles(JSONB, "sqlite")
        def _compile_jsonb_for_sqlite(element, compiler, **kwargs):
            return "JSON"
    except ImportError:
        pass

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    reference_time = datetime.now(timezone.utc).replace(microsecond=0)
    with TestSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="alpha",
                    severity="critical",
                    ecosystem="pypi",
                    published_at=reference_time - timedelta(hours=2),
                    fetched_at=reference_time - timedelta(hours=1),
                    source_url="https://example.test/alpha/1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="alpha",
                    severity="high",
                    ecosystem="npm",
                    published_at=reference_time - timedelta(hours=3),
                    fetched_at=reference_time - timedelta(hours=2),
                    source_url="https://example.test/alpha/2",
                ),
                VulnAdvisory(
                    id=3,
                    feed="beta",
                    severity="low",
                    ecosystem="pypi",
                    published_at=reference_time - timedelta(days=5),
                    fetched_at=reference_time - timedelta(hours=80),
                    source_url="https://example.test/beta/1",
                ),
                VulnAdvisory(
                    id=4,
                    feed="beta",
                    severity="medium",
                    ecosystem="npm",
                    published_at=reference_time - timedelta(days=6),
                    fetched_at=reference_time - timedelta(hours=90),
                    source_url="https://example.test/beta/2",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/vuln/freshness")
    def advisory_freshness(
        feed: Optional[str] = None,
        db: Session = Depends(get_session),
    ):
        return get_freshness_board(db, feed)

    def override_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/freshness")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["feeds"]) == 2
        feeds = {entry["feed"]: entry for entry in payload["feeds"]}
        assert feeds["alpha"]["severity_counts"]["critical"] == 1
        assert feeds["alpha"]["bucket"] == "fresh"

    print("PASS")