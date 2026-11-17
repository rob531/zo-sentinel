"""Acceptance self-test for the registry trust-band service.

Trust bands are fixed as follows:

- ``unknown``: ``trust_score IS NULL``
- ``low``: ``0 <= trust_score < 35``
- ``medium``: ``35 <= trust_score < 65``
- ``high``: ``65 <= trust_score < 85``
- ``critical``: ``trust_score >= 85``

Run with::

    python -m services.staged.registry_trust_band.contract
"""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    session = testing_session()
    session.add_all(
        [
            McpServerRegistry(
                server_id="registry-unknown",
                name="Unknown Trust Server",
                registry_source="alpha",
                trust_score=None,
                verdict="UNASSESSED",
                risk_tier="UNKNOWN",
                confidence=0.0,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="registry-low",
                name="Low Trust Server",
                registry_source="alpha",
                trust_score=10.0,
                verdict="MALICIOUS",
                risk_tier="HIGH",
                confidence=0.8,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="registry-medium",
                name="Medium Trust Server",
                registry_source="alpha",
                trust_score=40.0,
                verdict="SUSPICIOUS",
                risk_tier="MEDIUM",
                confidence=0.8,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="registry-high",
                name="High Trust Server",
                registry_source="beta",
                trust_score=70.0,
                verdict="TRUSTED",
                risk_tier="LOW",
                confidence=0.9,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="registry-critical",
                name="Critical Trust Server",
                registry_source="beta",
                trust_score=90.0,
                verdict="MALICIOUS",
                risk_tier="HIGH",
                confidence=0.9,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="registry-low-secondary",
                name="Another Low Trust Server",
                registry_source="beta",
                trust_score=20.0,
                verdict="SUSPICIOUS",
                risk_tier="MEDIUM",
                confidence=0.8,
                scan_count=1,
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

    client = TestClient(app)
    response = client.get("/api/registry/trust-bands")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["total"] == 6, body
    assert isinstance(body["bands"], list), body
    assert sum(item["count"] for item in body["bands"]) == 6, body
    unknown = next(item for item in body["bands"] if item["band"] == "unknown")
    assert unknown["count"] == 1, body
    assert isinstance(body["by_source"], list), body
    assert body["by_source"], body
    assert abs(sum(item["pct"] for item in body["bands"]) - 100.0) <= 0.1, body
    assert isinstance(body["generated_at"], str), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)