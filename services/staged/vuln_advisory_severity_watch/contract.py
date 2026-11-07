"""Acceptance self-test for the vulnerability advisory severity watch service."""
from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=1)

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="server-watch-1",
                name="Watch Server One",
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="server-watch-2",
                name="Watch Server Two",
                risk_tier="MEDIUM",
            ),
            VulnAdvisory(
                id=1,
                feed="test-feed",
                summary="Critical npm advisory",
                severity="critical",
                ecosystem="npm",
                package="critical-package",
                affected_ranges=[{"introduced": "1.0.0"}],
                aliases=["CVE-TEST-0001"],
                source_url="https://example.invalid/advisories/1",
                published_at=recent,
                fetched_at=recent,
                identities=["critical-package"],
                content_hash="watch-hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="test-feed",
                summary="High pip advisory",
                severity="high",
                ecosystem="pip",
                package="high-package",
                affected_ranges=[{"introduced": "2.0.0"}],
                aliases=["CVE-TEST-0002"],
                source_url="https://example.invalid/advisories/2",
                published_at=recent,
                fetched_at=recent,
                identities=["high-package"],
                content_hash="watch-hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="test-feed",
                summary="First low npm advisory",
                severity="low",
                ecosystem="npm",
                package="low-package-one",
                affected_ranges=[{"introduced": "3.0.0"}],
                aliases=["CVE-TEST-0003"],
                source_url="https://example.invalid/advisories/3",
                published_at=recent,
                fetched_at=recent,
                identities=["low-package-one"],
                content_hash="watch-hash-3",
            ),
            VulnAdvisory(
                id=4,
                feed="test-feed",
                summary="Second low npm advisory",
                severity="low",
                ecosystem="npm",
                package="low-package-two",
                affected_ranges=[{"introduced": "4.0.0"}],
                aliases=["CVE-TEST-0004"],
                source_url="https://example.invalid/advisories/4",
                published_at=recent,
                fetched_at=recent,
                identities=["low-package-two"],
                content_hash="watch-hash-4",
            ),
            VulnLink(
                id=1,
                advisory_id=1,
                server_id="server-watch-1",
                match_basis="package",
                match_value="critical-package",
                match_confidence=0.8,
                linked_at=recent,
            ),
            VulnLink(
                id=2,
                advisory_id=2,
                server_id="server-watch-1",
                match_basis="package",
                match_value="high-package",
                match_confidence=0.95,
                linked_at=recent,
            ),
            VulnLink(
                id=3,
                advisory_id=3,
                server_id="server-watch-2",
                match_basis="package",
                match_value="low-package-one",
                match_confidence=0.8,
                linked_at=recent,
            ),
        ]
    )
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

    with TestClient(app) as client:
        response = client.get("/api/vuln/advisories/severity-watch")
        assert response.status_code == 200, response.text
        body = response.json()

    assert body["totals"]["advisories"] == 4, body
    assert body["by_severity"]["critical"] == 1, body
    assert body["by_severity"]["low"] == 2, body
    assert {"ecosystem": "npm", "count": 3} in body["by_ecosystem"], body
    assert body["top_exposed_servers"][0]["server_id"] == "server-watch-1", body
    assert body["top_exposed_servers"][0]["max_confidence"] == 0.95, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)