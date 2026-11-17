"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

Run: python -m services.staged.vuln_advisory_identity_index.contract
"""
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

    session = TestingSession()
    session.add_all(
        [
            McpServerRegistry(
                server_id="server-cve-1",
                name="First linked server",
                risk_tier="HIGH",
                verdict="malicious",
            ),
            McpServerRegistry(
                server_id="server-cve-2",
                name="Second linked server",
                risk_tier="MEDIUM",
                verdict="suspicious",
            ),
            VulnAdvisory(
                id="advisory-cve-001",
                feed="osv",
                severity="HIGH",
                ecosystem="PyPI",
                package="example-package",
                aliases=["CVE-2024-0001"],
                identities=["CVE-2024-0001"],
                affected_ranges=[{"introduced": "0"}],
                published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                source_url="https://example.test/advisories/CVE-2024-0001",
            ),
            VulnAdvisory(
                id="advisory-other-001",
                feed="osv",
                severity="LOW",
                ecosystem="npm",
                package="other-package",
                aliases=["GHSA-other-001"],
                identities=["GHSA-other-001"],
                affected_ranges=[],
                published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
                source_url="https://example.test/advisories/GHSA-other-001",
            ),
            VulnLink(
                id=1,
                advisory_id="advisory-cve-001",
                server_id="server-cve-1",
                match_basis="alias",
                match_value="CVE-2024-0001",
                match_confidence=0.99,
                linked_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
            VulnLink(
                id=2,
                advisory_id="advisory-cve-001",
                server_id="server-cve-2",
                match_basis="identity",
                match_value="CVE-2024-0001",
                match_confidence=0.95,
                linked_at=datetime(2024, 1, 4, tzinfo=timezone.utc),
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
    response = client.get(
        "/api/vuln/advisories/identities",
        params={"q": "CVE-2024-0001"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["query"] == "CVE-2024-0001", body
    assert body["total"] == 1, body
    assert len(body["results"]) == 1, body

    result = body["results"][0]
    assert result["advisory_id"] == "advisory-cve-001", body
    assert result["feed"] == "osv", body
    assert result["severity"] == "HIGH", body
    assert result["package"] == "example-package", body
    assert result["ecosystem"] == "PyPI", body
    assert "CVE-2024-0001" in result["aliases"], body
    assert "CVE-2024-0001" in result["identities"], body

    linked_servers = result["linked_servers"]
    assert len(linked_servers) == 2, body
    assert {server["server_id"] for server in linked_servers} == {
        "server-cve-1",
        "server-cve-2",
    }, body

    unmatched = client.get(
        "/api/vuln/advisories/identities",
        params={"q": "CVE-1999-9999"},
    )
    assert unmatched.status_code == 200, unmatched.text
    assert unmatched.json()["results"] == [], unmatched.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)