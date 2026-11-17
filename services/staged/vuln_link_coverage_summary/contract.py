from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

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
    session.add(McpServerRegistry(server_id="srv-linked-1", name="Linked One"))
    session.add(McpServerRegistry(server_id="srv-linked-2", name="Linked Two"))
    session.add(McpServerRegistry(server_id="srv-unlinked", name="Unlinked"))

    session.add(
        VulnAdvisory(
            id="ADV-CRITICAL",
            feed="test-feed",
            summary="Critical advisory",
            severity="CRITICAL",
            ecosystem="python",
            package="package-a",
            affected_ranges=[{"introduced": "0", "fixed": "1.0.0"}],
            aliases=["CVE-2025-0001"],
            source_url="https://example.test/advisories/ADV-CRITICAL",
            published_at=now,
            fetched_at=now,
            identities={"cve": "CVE-2025-0001"},
            content_hash="critical-hash",
        )
    )
    session.add(
        VulnAdvisory(
            id="ADV-MEDIUM",
            feed="test-feed",
            summary="Medium advisory",
            severity="MEDIUM",
            ecosystem="npm",
            package="package-b",
            affected_ranges=[{"introduced": "0", "fixed": "2.0.0"}],
            aliases=["CVE-2025-0002"],
            source_url="https://example.test/advisories/ADV-MEDIUM",
            published_at=now,
            fetched_at=now,
            identities={"cve": "CVE-2025-0002"},
            content_hash="medium-hash",
        )
    )
    session.add(
        VulnLink(
            id=1,
            advisory_id="ADV-CRITICAL",
            server_id="srv-linked-1",
            match_basis="package",
            match_value="package-a",
            match_confidence=0.95,
            linked_at=now,
        )
    )
    session.add(
        VulnLink(
            id=2,
            advisory_id="ADV-CRITICAL",
            server_id="srv-linked-2",
            match_basis="cve",
            match_value="CVE-2025-0001",
            match_confidence=0.85,
            linked_at=now,
        )
    )
    session.add(
        VulnLink(
            id=3,
            advisory_id="ADV-MEDIUM",
            server_id="srv-linked-1",
            match_basis="package",
            match_value="package-b",
            match_confidence=0.75,
            linked_at=now,
        )
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
    response = client.get("/api/vuln/links/coverage?severity=SUMMARY")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_links"] == 3, body
    assert body["by_severity"]["CRITICAL"] == 2, body
    assert body["unlinked_servers"] == 1, body
    assert len(body["by_match_basis"]) == 2, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)