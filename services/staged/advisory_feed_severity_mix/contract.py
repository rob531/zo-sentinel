"""Acceptance self-test for the advisory feed severity mix service."""
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
    from app.models import VulnAdvisory

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now().replace(tzinfo=None)
    rows = [
        VulnAdvisory(
            id=1,
            feed="osv",
            summary="Critical advisory",
            severity="critical",
            ecosystem="npm",
            package="pkg-a",
            affected_ranges=">=1.0.0",
            aliases="CVE-2024-0001",
            source_url="https://example.test/1",
            published_at=now - timedelta(days=30),
            fetched_at=now,
            identities="pkg-a",
            content_hash="hash-1",
        ),
        VulnAdvisory(
            id=2,
            feed="osv",
            summary="High advisory",
            severity="high",
            ecosystem="pypi",
            package="pkg-b",
            affected_ranges=">=2.0.0",
            aliases="CVE-2024-0002",
            source_url="https://example.test/2",
            published_at=now - timedelta(days=20),
            fetched_at=now,
            identities="pkg-b",
            content_hash="hash-2",
        ),
        VulnAdvisory(
            id=3,
            feed="osv",
            summary="Medium advisory",
            severity="medium",
            ecosystem="npm",
            package="pkg-c",
            affected_ranges=">=3.0.0",
            aliases="CVE-2024-0003",
            source_url="https://example.test/3",
            published_at=now - timedelta(days=10),
            fetched_at=now,
            identities="pkg-c",
            content_hash="hash-3",
        ),
        VulnAdvisory(
            id=4,
            feed="github",
            summary="Critical advisory",
            severity="critical",
            ecosystem="pypi",
            package="pkg-d",
            affected_ranges=">=4.0.0",
            aliases="CVE-2024-0004",
            source_url="https://example.test/4",
            published_at=now - timedelta(days=1),
            fetched_at=now,
            identities="pkg-d",
            content_hash="hash-4",
        ),
        VulnAdvisory(
            id=5,
            feed="github",
            summary="High advisory",
            severity="high",
            ecosystem="npm",
            package="pkg-e",
            affected_ranges=">=5.0.0",
            aliases="CVE-2024-0005",
            source_url="https://example.test/5",
            published_at=now - timedelta(days=15),
            fetched_at=now,
            identities="pkg-e",
            content_hash="hash-5",
        ),
        VulnAdvisory(
            id=6,
            feed="github",
            summary="Advisory without severity",
            severity=None,
            ecosystem="pypi",
            package="pkg-f",
            affected_ranges=">=6.0.0",
            aliases="CVE-2024-0006",
            source_url="https://example.test/6",
            published_at=now - timedelta(days=25),
            fetched_at=now,
            identities="pkg-f",
            content_hash="hash-6",
        ),
    ]

    session = TestingSession()
    session.add_all(rows)
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
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