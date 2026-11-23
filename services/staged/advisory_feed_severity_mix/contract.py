"""contract.py -- acceptance self-test for the advisory feed severity mix service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, VulnAdvisory

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    old = now - timedelta(days=30)
    recent = now - timedelta(days=1)

    session = testing_session()
    session.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="feed-a",
                summary="critical advisory",
                severity="critical",
                ecosystem="pypi",
                package="alpha",
                affected_ranges="<1.0",
                aliases=None,
                source_url="https://example.test/a1",
                published_at=old,
                fetched_at=old,
                identities=None,
                content_hash="a1",
            ),
            VulnAdvisory(
                id=2,
                feed="feed-a",
                summary="high advisory",
                severity="high",
                ecosystem="npm",
                package="beta",
                affected_ranges="<2.0",
                aliases=None,
                source_url="https://example.test/a2",
                published_at=old,
                fetched_at=old,
                identities=None,
                content_hash="a2",
            ),
            VulnAdvisory(
                id=3,
                feed="feed-a",
                summary="medium advisory",
                severity="medium",
                ecosystem="pypi",
                package="gamma",
                affected_ranges="<3.0",
                aliases=None,
                source_url="https://example.test/a3",
                published_at=old,
                fetched_at=old,
                identities=None,
                content_hash="a3",
            ),
            VulnAdvisory(
                id=4,
                feed="feed-b",
                summary="advisory without severity",
                severity=None,
                ecosystem="npm",
                package="delta",
                affected_ranges="<4.0",
                aliases=None,
                source_url="https://example.test/b1",
                published_at=old,
                fetched_at=old,
                identities=None,
                content_hash="b1",
            ),
            VulnAdvisory(
                id=5,
                feed="feed-b",
                summary="recent advisory",
                severity="critical",
                ecosystem="npm",
                package="epsilon",
                affected_ranges="<5.0",
                aliases=None,
                source_url="https://example.test/b2",
                published_at=recent,
                fetched_at=recent,
                identities=None,
                content_hash="b2",
            ),
            VulnAdvisory(
                id=6,
                feed="feed-b",
                summary="another advisory",
                severity="high",
                ecosystem="pypi",
                package="zeta",
                affected_ranges="<6.0",
                aliases=None,
                source_url="https://example.test/b3",
                published_at=old,
                fetched_at=old,
                identities=None,
                content_hash="b3",
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as client:
        response = client.get("/api/vuln/advisory-feed-severity-mix")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["total"] == 6, body
    assert body["null_severity"] == 1, body
    assert body["recent_7d"] == 1, body
    assert sum(item["count"] for item in body["by_feed"]) == 6, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)