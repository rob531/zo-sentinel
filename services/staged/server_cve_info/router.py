# services/staged/server_cve_info/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_cve_info, ServerCveInfoResponse

router = APIRouter(prefix="/api", tags=["server_cve_info"])


@router.get(
    "/servers/{server_id}/cves",
    response_model=ServerCveInfoResponse,
    name="get_server_cve_info",
)
def server_cve_info(
    server_id: int,
    session: Session = Depends(get_session),
):
    """Return CVE information for a given server."""
    return get_server_cve_info(server_id, session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import McpServerRegistry, VulnAdvisory, VulnLink

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # ------------------------------------------------------------------- #
    ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=ENGINE, autocommit=False, autoflush=False)

    Base.metadata.create_all(bind=ENGINE)

    # Seed data: two servers, three CVEs linked to server 1
    with SessionLocal() as db:
        server1 = McpServerRegistry(
            server_id=1,
            name="test‑server‑1",
            confidence=0.9,
            description="first test server",
            first_seen=datetime.datetime.utcnow(),
            last_seen=datetime.datetime.utcnow(),
            last_scanned=datetime.datetime.utcnow(),
            last_assessed=datetime.datetime.utcnow(),
            meta={},
            registry_source="test",
            risk_tier="low",
            scan_count=1,
            trust_score=0.8,
            url="http://example.com/1",
            verdict="clean",
            verdict_reasoning="none",
        )
        server2 = McpServerRegistry(
            server_id=2,
            name="test‑server‑2",
            confidence=0.7,
            description="second test server",
            first_seen=datetime.datetime.utcnow(),
            last_seen=datetime.datetime.utcnow(),
            last_scanned=datetime.datetime.utcnow(),
            last_assessed=datetime.datetime.utcnow(),
            meta={},
            registry_source="test",
            risk_tier="medium",
            scan_count=1,
            trust_score=0.6,
            url="http://example.com/2",
            verdict="clean",
            verdict_reasoning="none",
        )
        db.add_all([server1, server2])

        adv1 = VulnAdvisory(
            id="CVE-0001",
            summary="Test vulnerability 1",
            severity="high",
            published_at=datetime.datetime(2023, 1, 1),
            affected_ranges="*",
            aliases=[],
            content_hash="hash1",
            ecosystem="pypi",
            feed="test",
            fetched_at=datetime.datetime.utcnow(),
            identities=[],
            package="pkg1",
            source_url="http://example.com/cve/1",
        )
        adv2 = VulnAdvisory(
            id="CVE-0002",
            summary="Test vulnerability 2",
            severity="medium",
            published_at=datetime.datetime(2023, 2, 1),
            affected_ranges="*",
            aliases=[],
            content_hash="hash2",
            ecosystem="pypi",
            feed="test",
            fetched_at=datetime.datetime.utcnow(),
            identities=[],
            package="pkg2",
            source_url="http://example.com/cve/2",
        )
        adv3 = VulnAdvisory(
            id="CVE-0003",
            summary="Test vulnerability 3",
            severity="low",
            published_at=datetime.datetime(2023, 3, 1),
            affected_ranges="*",
            aliases=[],
            content_hash="hash3",
            ecosystem="pypi",
            feed="test",
            fetched_at=datetime.datetime.utcnow(),
            identities=[],
            package="pkg3",
            source_url="http://example.com/cve/3",
        )
        db.add_all([adv1, adv2, adv3])

        link1 = VulnLink(
            id=1,
            server_id=1,
            advisory_id="CVE-0001",
            linked_at=datetime.datetime.utcnow(),
            match_basis="test",
            match_confidence=1.0,
            match_value="test",
        )
        link2 = VulnLink(
            id=2,
            server_id=1,
            advisory_id="CVE-0002",
            linked_at=datetime.datetime.utcnow(),
            match_basis="test",
            match_confidence=1.0,
            match_value="test",
        )
        link3 = VulnLink(
            id=3,
            server_id=1,
            advisory_id="CVE-0003",
            linked_at=datetime.datetime.utcnow(),
            match_basis="test",
            match_confidence=1.0,
            match_value="test",
        )
        db.add_all([link1, link2, link3])

        db.commit()

    # Dependency override that yields a fresh session per request
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build FastAPI app with router and override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/servers/1/cves")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "server" in data, "Missing server key"
    assert "cves" in data, "Missing cves key"
    assert len(data["cves"]) == 3, f"Expected 3 CVEs, got {len(data['cves'])}"
    print("PASS")