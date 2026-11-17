"""Acceptance self-test for the vuln advisory digest API."""
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
    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(server_id="srv-1", name="Registry One", risk_tier="HIGH"),
            McpServerRegistry(server_id="srv-2", name="Registry Two", risk_tier="MEDIUM"),
            VulnAdvisory(
                id="adv-high",
                feed="test-feed",
                summary="High severity advisory",
                severity="high",
                ecosystem="npm",
                package="example-package",
                affected_ranges=[{"introduced": "1.0.0"}],
                aliases=["CVE-2026-0001"],
                source_url="https://example.test/advisories/adv-high",
                published_at=now,
                fetched_at=now,
                identities=["GHSA-test-high"],
                content_hash="high-hash",
            ),
            VulnAdvisory(
                id="adv-low",
                feed="test-feed",
                summary="Low severity advisory",
                severity="low",
                ecosystem="pypi",
                package="low-package",
                affected_ranges=[{"introduced": "2.0.0"}],
                aliases=["CVE-2026-0002"],
                source_url="https://example.test/advisories/adv-low",
                published_at=now,
                fetched_at=now,
                identities=["GHSA-test-low"],
                content_hash="low-hash",
            ),
            VulnAdvisory(
                id="adv-medium",
                feed="test-feed",
                summary="Medium severity advisory",
                severity="medium",
                ecosystem="npm",
                package="medium-package",
                affected_ranges=[{"introduced": "3.0.0"}],
                aliases=["CVE-2026-0003"],
                source_url="https://example.test/advisories/adv-medium",
                published_at=now,
                fetched_at=now,
                identities=["GHSA-test-medium"],
                content_hash="medium-hash",
            ),
            VulnLink(
                id=1,
                advisory_id="adv-high",
                server_id="srv-1",
                match_basis="package",
                match_value="example-package",
                match_confidence=0.99,
                linked_at=now,
            ),
            VulnLink(
                id=2,
                advisory_id="adv-high",
                server_id="srv-2",
                match_basis="alias",
                match_value="CVE-2026-0001",
                match_confidence=0.95,
                linked_at=now,
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

    client = TestClient(app)

    response = client.get("/api/vuln/advisories")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 3, body
    assert body["total"] == 3, body
    assert len(body["advisories"]) == 3, body
    assert sum(item["linked_servers"] for item in body["advisories"]) == 2, body

    response = client.get("/api/vuln/advisories", params={"severity": "high"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1, body
    assert body["total"] == 1, body
    assert len(body["advisories"]) == 1, body
    advisory = body["advisories"][0]
    assert advisory["id"] == "adv-high", body
    assert advisory["severity"] == "high", body
    assert advisory["linked_servers"] == 2, body
    assert len(advisory["links"]) == 2, body

    response = client.get("/api/vuln/advisories/adv-high")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["advisory"]["id"] == "adv-high", body
    assert body["advisory"]["linked_servers"] == 2, body
    assert len(body["servers"]) == 2, body
    assert {server["server_id"] for server in body["servers"]} == {"srv-1", "srv-2"}, body

    response = client.get("/api/vuln/advisories/unknown-advisory")
    assert response.status_code == 404, response.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)