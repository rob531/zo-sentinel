# services/staged/server_cve_search_api/router.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_cves
from .contract import ServerCveResponse

router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/cves",
    response_model=ServerCveResponse,
    summary="Get CVEs for a given server",
)
def read_server_cves(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerCveResponse:
    """
    Retrieve CVE information for the specified server.
    """
    try:
        return get_server_cves(db, server_id)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Import the declarative base and the concrete models used by the logic.
    from app.db import Base, get_session as original_get_session
    from app.models import VulnAdvisory, VulnLink

    # --------------------------------------------------------------------- #
    # Create an in‑memory SQLite database and bind a session factory to it.
    # --------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # --------------------------------------------------------------------- #
    # Dependency override that yields a SQLite session instead of the real DB.
    # --------------------------------------------------------------------- #
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # Build a minimal FastAPI app, inject the router, and run the test client.
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[original_get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Seed the in‑memory database with two advisories and two links for
    # server 'srv-001'.
    # --------------------------------------------------------------------- #
    with SessionLocal() as db:
        adv1 = VulnAdvisory(
            id="CVE-1234-5678",
            feed="NVD",
            summary="Test CVE 1",
            severity="HIGH",
            ecosystem="python",
            package="pkg1",
            source_url="http://example.com/1",
            published_at="2023-01-01T00:00:00Z",
        )
        adv2 = VulnAdvisory(
            id="CVE-8765-4321",
            feed="NVD",
            summary="Test CVE 2",
            severity="MEDIUM",
            ecosystem="go",
            package="pkg2",
            source_url="http://example.com/2",
            published_at="2023-02-01T00:00:00Z",
        )
        db.add_all([adv1, adv2])
        db.flush()  # ensure IDs are persisted before linking

        link1 = VulnLink(server_id="srv-001", advisory_id=adv1.id)
        link2 = VulnLink(server_id="srv-001", advisory_id=adv2.id)
        db.add_all([link1, link2])
        db.commit()

    # --------------------------------------------------------------------- #
    # Perform the request and validate the response.
    # --------------------------------------------------------------------- #
    response = client.get("/api/servers/srv-001/cves")
    if response.status_code != 200:
        sys.exit(f"Unexpected status code: {response.status_code}")

    payload = response.json()
    assert payload.get("server_id") == "srv-001"
    cves = payload.get("cves", [])
    assert isinstance(cves, list) and len(cves) >= 1
    ids = {c.get("id") for c in cves}
    assert "CVE-1234-5678" in ids

    print("PASS")