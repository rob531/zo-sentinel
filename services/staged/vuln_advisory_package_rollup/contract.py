"""Acceptance self-test for the vuln advisory package rollup service."""
from __future__ import annotations

import sys
from datetime import datetime


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
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-alpha",
                name="Alpha Server",
                risk_tier="HIGH",
                trust_score=0.91,
                verdict="TRUSTED",
            ),
            McpServerRegistry(
                server_id="srv-beta",
                name="Beta Server",
                risk_tier="MEDIUM",
                trust_score=0.74,
                verdict="TRUSTED",
            ),
            VulnAdvisory(
                id=101,
                feed="NVD",
                summary="Critical requests advisory",
                severity="critical",
                ecosystem="python",
                package="requests",
                affected_ranges=[{"introduced": "0"}],
                aliases=["python-requests"],
                source_url="https://example.test/advisories/101",
                published_at=datetime(2025, 1, 10, 12, 0, 0),
                fetched_at=datetime(2025, 1, 10, 12, 5, 0),
                identities=["CVE-TEST-101"],
                content_hash="hash-101",
            ),
            VulnAdvisory(
                id=102,
                feed="NVD",
                summary="High requests advisory",
                severity="high",
                ecosystem="python",
                package="requests",
                affected_ranges=[{"introduced": "1.0"}],
                aliases=[],
                source_url="https://example.test/advisories/102",
                published_at=datetime(2025, 1, 11, 12, 0, 0),
                fetched_at=datetime(2025, 1, 11, 12, 5, 0),
                identities=["CVE-TEST-102"],
                content_hash="hash-102",
            ),
            VulnAdvisory(
                id=201,
                feed="NVD",
                summary="Critical lodash advisory",
                severity="critical",
                ecosystem="npm",
                package="lodash",
                affected_ranges=[{"introduced": "0"}],
                aliases=[],
                source_url="https://example.test/advisories/201",
                published_at=datetime(2025, 1, 9, 12, 0, 0),
                fetched_at=datetime(2025, 1, 9, 12, 5, 0),
                identities=["CVE-TEST-201"],
                content_hash="hash-201",
            ),
            VulnLink(
                id=1001,
                advisory_id=101,
                server_id="srv-alpha",
                match_basis="package",
                match_value="requests",
                match_confidence=1.0,
                linked_at=datetime(2025, 1, 10, 13, 0, 0),
            ),
            VulnLink(
                id=1002,
                advisory_id=102,
                server_id="srv-beta",
                match_basis="package",
                match_value="requests",
                match_confidence=0.95,
                linked_at=datetime(2025, 1, 11, 13, 0, 0),
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    response = TestClient(app).get("/api/vuln/packages")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_packages"] == 2, body
    packages = body["packages"]
    assert len(packages) == 2, body

    python_package = next(item for item in packages if item["ecosystem"] == "python")
    assert python_package["advisory_count"] == 2, body
    assert python_package["severity_counts"]["critical"] == 1, body
    assert python_package["affected_servers"] == 2, body
    assert packages[0]["ecosystem"] == "python", body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)