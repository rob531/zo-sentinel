"""Acceptance self-test for the vulnerability advisory staleness rollup service."""
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
    from app.models import Base, VulnAdvisory, VulnLink

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    old = now - timedelta(days=40)
    recent = now - timedelta(days=1)

    session = TestingSession()
    session.add_all(
        [
            VulnAdvisory(
                id=101,
                feed="osv",
                severity="HIGH",
                ecosystem="PyPI",
                package="example-a",
                published_at=now - timedelta(days=2),
                fetched_at=recent,
                content_hash="hash-101",
                source_url="https://example.test/advisories/101",
            ),
            VulnAdvisory(
                id=102,
                feed="osv",
                severity="MEDIUM",
                ecosystem="npm",
                package="example-b",
                published_at=now - timedelta(days=3),
                fetched_at=recent,
                content_hash="hash-102",
                source_url="https://example.test/advisories/102",
            ),
            VulnAdvisory(
                id=103,
                feed="osv",
                severity="LOW",
                ecosystem="PyPI",
                package="example-c",
                published_at=now - timedelta(days=4),
                fetched_at=old,
                content_hash="hash-103",
                source_url="https://example.test/advisories/103",
            ),
            VulnAdvisory(
                id=104,
                feed="github",
                severity="CRITICAL",
                ecosystem="npm",
                package="example-d",
                published_at=now - timedelta(days=5),
                fetched_at=recent,
                content_hash="hash-104",
                source_url="https://example.test/advisories/104",
            ),
            VulnAdvisory(
                id=105,
                feed="github",
                severity="HIGH",
                ecosystem="PyPI",
                package="example-e",
                published_at=now - timedelta(days=6),
                fetched_at=recent,
                content_hash="hash-105",
                source_url="https://example.test/advisories/105",
            ),
        ]
    )
    session.add_all(
        [
            VulnLink(
                id=201,
                advisory_id=101,
                server_id="server-1",
                match_basis="package",
                match_confidence=0.99,
                match_value="example-a",
                linked_at=now,
            ),
            VulnLink(
                id=202,
                advisory_id=102,
                server_id="server-2",
                match_basis="package",
                match_confidence=0.95,
                match_value="example-b",
                linked_at=now,
            ),
            VulnLink(
                id=203,
                advisory_id=103,
                server_id="server-3",
                match_basis="package",
                match_confidence=0.90,
                match_value="example-c",
                linked_at=now,
            ),
        ]
    )
    session.commit()
    session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/vuln/advisory-staleness", params={"days": 30})
    assert response.status_code == 200, response.text

    body = response.json()
    feeds = body["feeds"]
    assert sum(feed["orphans"] for feed in feeds) == 2, body
    assert sum(feed["stale"] for feed in feeds) == 1, body
    assert sum(feed["advisories"] for feed in feeds) == 5, body
    assert sum(
        sum(feed["severity_counts"].values()) for feed in feeds
    ) == 5, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)