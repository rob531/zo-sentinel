from __future__ import annotations

import sys


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
                server_id="srv-a",
                name="Server A",
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="srv-b",
                name="Server B",
                risk_tier="MEDIUM",
            ),
            McpServerRegistry(
                server_id="srv-c",
                name="Server C",
                risk_tier="LOW",
            ),
            VulnAdvisory(
                id="adv-critical",
                feed="example-feed",
                summary="Critical advisory",
                severity="critical",
                ecosystem="npm",
                package="critical-package",
                source_url="https://example.test/critical",
            ),
            VulnAdvisory(
                id="adv-high-1",
                feed="example-feed",
                summary="High advisory one",
                severity="high",
                ecosystem="npm",
                package="high-package-one",
                source_url="https://example.test/high-one",
            ),
            VulnAdvisory(
                id="adv-high-2",
                feed="example-feed",
                summary="High advisory two",
                severity="high",
                ecosystem="pypi",
                package="high-package-two",
                source_url="https://example.test/high-two",
            ),
            VulnLink(
                id=1,
                advisory_id="adv-critical",
                server_id="srv-a",
                match_basis="package",
                match_value="critical-package",
                match_confidence=0.9,
            ),
            VulnLink(
                id=2,
                advisory_id="adv-high-1",
                server_id="srv-a",
                match_basis="package",
                match_value="high-package-one",
                match_confidence=0.6,
            ),
            VulnLink(
                id=3,
                advisory_id="adv-high-1",
                server_id="srv-b",
                match_basis="package",
                match_value="high-package-one",
                match_confidence=0.2,
            ),
            VulnLink(
                id=4,
                advisory_id="adv-high-2",
                server_id="srv-c",
                match_basis="package",
                match_value="high-package-two",
                match_confidence=0.9,
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
    response = client.get("/api/vuln/coverage")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["totals"]["links"] == 4, body
    assert body["totals"]["advisories"] == 3, body
    assert body["totals"]["servers"] == 3, body

    severity_counts = {
        item["severity"]: item["count"] for item in body["by_severity"]
    }
    assert severity_counts["critical"] == 1, body

    top_servers = body["top_servers"]
    assert top_servers, body
    assert top_servers[0]["link_count"] == max(
        item["link_count"] for item in top_servers
    ), body
    assert top_servers[0]["server_id"] == "srv-a", body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)