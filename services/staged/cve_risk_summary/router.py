from fastapi import APIRouter, Depends
from app.db import get_session
from .logic import get_cve_risk_summary

router = APIRouter(prefix="/api")


@router.get("/cve/risk-summary")
def cve_risk_summary(session=Depends(get_session)):
    """
    Returns a summary of CVE risk across the ecosystem.
    """
    return get_cve_risk_summary(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import (
        VulnAdvisory,
        VulnLink,
        McpServerRegistry,
    )

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite database and override the session dependency
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    with SessionLocal() as db:
        # Servers
        srv1 = McpServerRegistry(id=1, name="server‑one", ecosystem="linux")
        srv2 = McpServerRegistry(id=2, name="server‑two", ecosystem="windows")
        srv3 = McpServerRegistry(id=3, name="server‑three", ecosystem="linux")
        db.add_all([srv1, srv2, srv3])

        # Advisories
        adv1 = VulnAdvisory(id=1, severity="CRITICAL")
        adv2 = VulnAdvisory(id=2, severity="HIGH")
        adv3 = VulnAdvisory(id=3, severity="MEDIUM")
        adv4 = VulnAdvisory(id=4, severity="LOW")
        db.add_all([adv1, adv2, adv3, adv4])

        # Links (advisory -> server)
        link1 = VulnLink(id=1, advisory_id=1, server_id=1)  # CRITICAL on srv1
        link2 = VulnLink(id=2, advisory_id=2, server_id=1)  # HIGH on srv1
        link3 = VulnLink(id=3, advisory_id=3, server_id=2)  # MEDIUM on srv2
        link4 = VulnLink(id=4, advisory_id=4, server_id=3)  # LOW on srv3
        db.add_all([link1, link2, link3, link4])

        db.commit()

    # ------------------------------------------------------------------- #
    # Execute request against the test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/cve/risk-summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Expected severity totals based on the seeded advisories
    expected_by_severity = {
        "CRITICAL": 1,
        "HIGH": 1,
        "MEDIUM": 1,
        "LOW": 1,
    }

    assert data["total_advisories"] == 4, "Total advisories mismatch"
    assert data["by_severity"] == expected_by_severity, "Severity breakdown mismatch"

    # Basic sanity checks for other fields
    assert isinstance(data.get("top_affected_servers"), list), "Missing top_affected_servers"
    assert isinstance(data.get("ecosystem_breakdown"), dict), "Missing ecosystem_breakdown"

    print("PASS")