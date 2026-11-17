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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    name="Server One",
                    url="https://server-one.example",
                    risk_tier="HIGH",
                    verdict="trusted",
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    name="Server Two",
                    url="https://server-two.example",
                    risk_tier="MEDIUM",
                    verdict="review",
                ),
                VulnAdvisory(
                    id="advisory-shared",
                    feed="NVD",
                    summary="Shared advisory",
                    severity="HIGH",
                    ecosystem="npm",
                    package="example-package",
                    affected_ranges="<1.2.3",
                    aliases="CVE-2025-0001",
                    source_url="https://example.test/advisory-shared",
                    published_at=datetime(2025, 1, 1),
                ),
                VulnAdvisory(
                    id="advisory-unlinked",
                    feed="NVD",
                    summary="Advisory with no links",
                    severity="LOW",
                    ecosystem="PyPI",
                    package="unused-package",
                    affected_ranges="<2.0.0",
                    aliases="CVE-2025-0002",
                    source_url="https://example.test/advisory-unlinked",
                    published_at=datetime(2025, 1, 2),
                ),
                VulnLink(
                    id=1,
                    advisory_id="advisory-shared",
                    server_id="srv-1",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.95,
                    linked_at=datetime(2025, 1, 3),
                ),
                VulnLink(
                    id=2,
                    advisory_id="advisory-shared",
                    server_id="srv-2",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.90,
                    linked_at=datetime(2025, 1, 3),
                ),
                VulnLink(
                    id=3,
                    advisory_id="advisory-without-row",
                    server_id="srv-1",
                    match_basis="package",
                    match_value="missing-package",
                    match_confidence=0.80,
                    linked_at=datetime(2025, 1, 3),
                ),
            ]
        )
        session.commit()
    finally:
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

    response = client.get("/api/vuln/servers/srv-1/links")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_id"] == "srv-1", body
    assert body["count"] == 1, body
    assert len(body["links"]) == 1, body
    assert body["links"][0]["advisory_id"] == "advisory-shared", body
    assert body["links"][0]["match_confidence"] == 0.95, body
    assert body["links"][0]["server_name"] == "Server One", body
    assert body["links"][0]["server_risk_tier"] == "HIGH", body

    response = client.get("/api/vuln/servers/srv-2/links")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["server_id"] == "srv-2", body
    assert body["count"] == 1, body
    assert len(body["links"]) == 1, body
    assert body["links"][0]["advisory_id"] == "advisory-shared", body
    assert body["links"][0]["match_confidence"] == 0.90, body
    assert body["links"][0]["server_name"] == "Server Two", body
    assert body["links"][0]["server_risk_tier"] == "MEDIUM", body

    response = client.get("/api/vuln/links/advisory-shared")
    assert response.status_code == 200, response.text

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)