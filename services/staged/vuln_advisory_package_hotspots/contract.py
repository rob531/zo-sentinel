"""Acceptance self-test for the vuln advisory package hotspots service."""
from __future__ import annotations

import sys
from datetime import datetime, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, VulnAdvisory, VulnLink

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    s = TestingSession()
    s.add_all(
        [
            VulnAdvisory(
                id=1,
                feed="test-feed",
                summary="Critical advisory",
                severity="critical",
                ecosystem="pypi",
                package="requests",
                affected_ranges=["<2.0"],
                aliases=["req"],
                source_url="https://example.test/advisories/1",
                published_at=now,
                fetched_at=now,
                identities=["CVE-TEST-0001"],
                content_hash="hash-1",
            ),
            VulnAdvisory(
                id=2,
                feed="test-feed",
                summary="Medium advisory",
                severity="medium",
                ecosystem="pypi",
                package="requests",
                affected_ranges=["<3.0"],
                aliases=[],
                source_url="https://example.test/advisories/2",
                published_at=now,
                fetched_at=now,
                identities=["CVE-TEST-0002"],
                content_hash="hash-2",
            ),
            VulnAdvisory(
                id=3,
                feed="test-feed",
                summary="High advisory",
                severity="high",
                ecosystem="pypi",
                package="flask",
                affected_ranges=["<1.0"],
                aliases=[],
                source_url="https://example.test/advisories/3",
                published_at=now,
                fetched_at=now,
                identities=["CVE-TEST-0003"],
                content_hash="hash-3",
            ),
            VulnAdvisory(
                id=4,
                feed="test-feed",
                summary="Low advisory",
                severity="low",
                ecosystem="npm",
                package="left-pad",
                affected_ranges=["<1.0"],
                aliases=[],
                source_url="https://example.test/advisories/4",
                published_at=now,
                fetched_at=now,
                identities=["CVE-TEST-0004"],
                content_hash="hash-4",
            ),
        ]
    )
    s.flush()
    s.add_all(
        [
            VulnLink(
                id=1,
                advisory_id=1,
                server_id="srv-1",
                match_basis="package",
                match_value="requests",
                match_confidence=0.99,
                linked_at=now,
            ),
            VulnLink(
                id=2,
                advisory_id=1,
                server_id="srv-2",
                match_basis="package",
                match_value="requests",
                match_confidence=0.98,
                linked_at=now,
            ),
            VulnLink(
                id=3,
                advisory_id=2,
                server_id="srv-3",
                match_basis="package",
                match_value="requests",
                match_confidence=0.97,
                linked_at=now,
            ),
        ]
    )
    s.commit()
    s.close()

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
    response = client.get(
        "/api/vuln/packages",
        params={"ecosystem": "pypi", "limit": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1, body
    assert body["packages"], body

    top = body["packages"][0]
    assert top["package"] == "requests", body
    assert top["advisories"] == 2, body
    assert top["max_severity"] == "critical", body
    assert top["linked_servers"] == 3, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)