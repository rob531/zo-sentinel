from datetime import datetime, timedelta
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

from .router import router


def run_contract() -> int:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    test_app = FastAPI()
    route_paths = {getattr(route, "path", "") for route in router.routes}
    target_path = "/api/vuln/advisories/freshness-gap"
    prefix = "" if target_path in route_paths else "/api"
    test_app.include_router(router, prefix=prefix)

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.utcnow()
    stale_at = now - timedelta(days=30)
    fresh_at = now - timedelta(hours=1)

    advisories = [
        VulnAdvisory(
            id=1,
            feed="seeded-feed",
            ecosystem="npm",
            severity="high",
            source_url="https://example.test/advisories/1",
            published_at=stale_at,
            fetched_at=stale_at,
        ),
        VulnAdvisory(
            id=2,
            feed="seeded-feed",
            ecosystem="npm",
            severity="critical",
            source_url="https://example.test/advisories/2",
            published_at=stale_at,
            fetched_at=stale_at,
        ),
        VulnAdvisory(
            id=3,
            feed="seeded-feed",
            ecosystem="npm",
            severity="high",
            source_url="https://example.test/advisories/3",
            published_at=stale_at,
            fetched_at=stale_at,
        ),
        VulnAdvisory(
            id=4,
            feed="seeded-feed",
            ecosystem="npm",
            severity="medium",
            source_url="https://example.test/advisories/4",
            published_at=fresh_at,
            fetched_at=fresh_at,
        ),
        VulnAdvisory(
            id=5,
            feed="other-feed",
            ecosystem="pypi",
            severity="high",
            source_url="https://example.test/advisories/5",
            published_at=fresh_at,
            fetched_at=fresh_at,
        ),
        VulnAdvisory(
            id=6,
            feed="other-feed",
            ecosystem="pypi",
            severity="medium",
            source_url="https://example.test/advisories/6",
            published_at=fresh_at,
            fetched_at=fresh_at,
        ),
    ]
    links = [
        VulnLink(
            advisory_id=1,
            server_id="server-a",
            match_value="example-a",
            match_basis="package",
            match_confidence=1.0,
            linked_at=now,
        ),
        VulnLink(
            advisory_id=2,
            server_id="server-b",
            match_value="example-b",
            match_basis="package",
            match_confidence=0.9,
            linked_at=now,
        ),
        VulnLink(
            advisory_id=5,
            server_id="server-c",
            match_value="example-c",
            match_basis="package",
            match_confidence=0.8,
            linked_at=now,
        ),
        VulnLink(
            advisory_id=6,
            server_id="server-d",
            match_value="example-d",
            match_basis="package",
            match_confidence=1.0,
            linked_at=now,
        ),
    ]

    with session_factory() as session:
        session.add_all(advisories)
        session.flush()
        session.add_all(links)
        session.commit()

    try:
        with TestClient(test_app) as client:
            response = client.get(target_path, params={"days": 7})

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 7
        assert len(payload["feeds"]) == 2

        feeds = {item["feed"]: item for item in payload["feeds"]}
        assert "seeded-feed" in feeds
        assert "other-feed" in feeds
        assert feeds["seeded-feed"]["unlinked"] == 2
        assert feeds["seeded-feed"]["stale"] >= 3
        assert feeds["seeded-feed"]["total"] == 4
        assert feeds["seeded-feed"]["median_staleness_hours"] >= 0
    finally:
        engine.dispose()

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run_contract())