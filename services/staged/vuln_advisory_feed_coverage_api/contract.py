from collections import defaultdict
from typing import Any, Dict

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter()


def _feed_column() -> Any:
    columns = VulnAdvisory.__table__.columns
    preferred = (
        "source_feed",
        "feed",
        "feed_name",
        "feed_source",
        "advisory_feed",
        "source",
    )
    for name in preferred:
        if name in columns:
            return columns[name]
    for column in columns:
        if "feed" in column.key.lower():
            return column
    raise RuntimeError("VulnAdvisory does not expose a source-feed column")


@router.get("/api/vuln/advisories/feed-coverage")
def get_feed_coverage(session: Session = Depends(get_session)) -> Dict[str, Any]:
    feed_column = _feed_column()
    severity_column = VulnAdvisory.__table__.columns["severity"]

    rows = (
        session.query(feed_column, severity_column, func_count())
        .group_by(feed_column, severity_column)
        .all()
    )

    feed_counts: Dict[str, int] = defaultdict(int)
    severity_counts: Dict[str, int] = defaultdict(int)
    feed_severities: Dict[str, Dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for feed, severity, count in rows:
        feed_name = "unknown" if feed is None else str(feed)
        severity_name = "unknown" if severity is None else str(severity)
        count = int(count or 0)
        feed_counts[feed_name] += count
        severity_counts[severity_name] += count
        feed_severities[feed_name][severity_name] += count

    feeds = {
        feed: {
            "count": feed_counts[feed],
            "severity_breakdown": dict(
                sorted(feed_severities[feed].items(), key=lambda item: item[0])
            ),
        }
        for feed in sorted(feed_counts)
    }

    return {
        "total_advisories": sum(feed_counts.values()),
        "unique_feeds": len(feed_counts),
        "feeds": feeds,
        "severities": dict(sorted(severity_counts.items())),
    }


def func_count() -> Any:
    from sqlalchemy import func

    return func.count()


def _build_self_test_app() -> FastAPI:
    metadata = MetaData()
    feed_column = _feed_column()
    severity_column = VulnAdvisory.__table__.columns["severity"]

    advisory_table = Table(
        VulnAdvisory.__tablename__,
        metadata,
        Column(feed_column.name, String, nullable=True),
        Column(severity_column.name, String, nullable=True),
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)

    session = Session(engine)
    session.execute(
        insert(advisory_table),
        [
            {feed_column.name: "nvd", severity_column.name: "HIGH"},
            {feed_column.name: "nvd", severity_column.name: "HIGH"},
            {feed_column.name: "nvd", severity_column.name: "CRITICAL"},
            {feed_column.name: "osv", severity_column.name: "MEDIUM"},
        ],
    )
    session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: session
    that_app.state.test_session = session
    that_app.state.test_engine = engine
    return that_app


def _run_self_test() -> None:
    that_app = _build_self_test_app()
    try:
        with TestClient(that_app) as client:
            response = client.get("/api/vuln/advisories/feed-coverage")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_advisories"] == 4, payload
            assert payload["unique_feeds"] == 2, payload
            assert payload["severities"] == {
                "CRITICAL": 1,
                "HIGH": 2,
                "MEDIUM": 1,
            }, payload
            assert payload["feeds"]["nvd"]["count"] == 3, payload
            assert payload["feeds"]["nvd"]["severity_breakdown"] == {
                "CRITICAL": 1,
                "HIGH": 2,
            }, payload
            assert payload["feeds"]["osv"] == {
                "count": 1,
                "severity_breakdown": {"MEDIUM": 1},
            }, payload
    finally:
        that_app.state.test_session.close()
        that_app.state.test_engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()