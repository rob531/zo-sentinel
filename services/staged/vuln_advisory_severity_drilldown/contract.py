"""contract.py -- the service's ACCEPTANCE self-test (the LIVENESS gate).

This is what makes a service "spineful": the promotion gate
(tools/promote_staged_to_active.py) runs THIS file in an isolated subprocess --
`python -m services.<stage>.<name>.contract` -- and a zero exit is the proof
that the service boots, mounts, serves 200, and returns a schema-valid body over
the REAL data layer (overridden to an in-memory SQLite for hermeticity). A
non-zero exit keeps the service in staged/.

It must FAIL LOUD, never degrade: an import/env problem is a FAIL (exit 1), not a
silent skip. That is the deliberate inverse of the 74% Tier-0 degradation
(FU-031) that let presence pass for correctness.

Run:  python -m services.staged.vuln_advisory_severity_drilldown.contract
    ->  prints PASS / FAIL, exits 0/1
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

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TestingSession()
    s.add(
        VulnAdvisory(
            id=1,
            feed="osv",
            summary="Test advisory",
            severity="HIGH",
            ecosystem="pypi",
            package="example-package",
            affected_ranges=[{"introduced": "1.0.0", "fixed": "1.2.0"}],
            aliases=["CVE-2026-0001"],
            source_url="https://example.test/advisories/1",
            published_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv-high",
            name="High Risk Server",
            risk_tier="HIGH",
            verdict="BLOCK",
            trust_score=0.95,
        )
    )
    s.add(
        McpServerRegistry(
            server_id="srv-low",
            name="Low Risk Server",
            risk_tier="LOW",
            verdict="ALLOW",
            trust_score=0.75,
        )
    )
    s.add(
        VulnLink(
            id=1,
            advisory_id=1,
            server_id="srv-high",
            match_basis="package",
            match_value="example-package",
            match_confidence=0.99,
        )
    )
    s.add(
        VulnLink(
            id=2,
            advisory_id=1,
            server_id="srv-low",
            match_basis="alias",
            match_value="CVE-2026-0001",
            match_confidence=0.85,
        )
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
    response = client.get("/api/vuln/advisories/1/drilldown")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["advisory"]["id"] == 1, body
    assert body["advisory"]["severity"] == "HIGH", body
    assert len(body["affected_servers"]) == 2, body
    assert body["counts"]["servers"] == 2, body
    assert body["counts"]["by_tier"] == {"HIGH": 1, "LOW": 1}, body

    servers = {item["server_id"]: item for item in body["affected_servers"]}
    assert set(servers) == {"srv-high", "srv-low"}, body
    assert servers["srv-high"]["risk_tier"] == "HIGH", body
    assert servers["srv-low"]["risk_tier"] == "LOW", body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- FAIL LOUD, never degrade
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)