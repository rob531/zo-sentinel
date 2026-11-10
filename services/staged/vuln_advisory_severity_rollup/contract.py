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
            VulnAdvisory(
                id="adv-critical",
                feed="test-feed",
                summary="Critical advisory",
                severity="CRITICAL",
                ecosystem="python",
                package="critical-package",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.test/critical",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="critical-hash",
            ),
            VulnAdvisory(
                id="adv-high-one",
                feed="test-feed",
                summary="High advisory one",
                severity="HIGH",
                ecosystem="python",
                package="high-package",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.test/high-one",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="high-one-hash",
            ),
            VulnAdvisory(
                id="adv-high-two",
                feed="test-feed",
                summary="High advisory two",
                severity="HIGH",
                ecosystem="npm",
                package="another-package",
                affected_ranges=[],
                aliases=[],
                source_url="https://example.test/high-two",
                published_at=now,
                fetched_at=now,
                identities=[],
                content_hash="high-two-hash",
            ),
            VulnLink(
                id=1,
                advisory_id="adv-critical",
                server_id="server-1",
                match_basis="package",
                match_value="critical-package",
                match_confidence=1.0,
                linked_at=now,
            ),
            VulnLink(
                id=2,
                advisory_id="adv-high-one",
                server_id="server-1",
                match_basis="package",
                match_value="high-package",
                match_confidence=0.95,
                linked_at=now,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    client = TestClient(test_app)
    response = client.get("/api/vuln/advisories/severity-rollup")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_advisories"] == 3, body

    severity_buckets = {item["severity"]: item for item in body["by_severity"]}
    assert severity_buckets["HIGH"]["advisories"] == 2, body
    assert severity_buckets["CRITICAL"]["linked_servers"] == 1, body
    assert body["top_packages"], body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)