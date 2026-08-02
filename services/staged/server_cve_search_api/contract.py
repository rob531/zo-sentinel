# services/staged/server_cve_search_api/contract.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

# Real data layer imports (must remain unchanged for production)
from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api")


class CVEInfo(BaseModel):
    id: str = Field(..., description="CVE identifier")
    feed: Optional[str] = Field(None, description="Feed source")
    summary: Optional[str] = Field(None, description="Short description")
    severity: Optional[str] = Field(None, description="Severity rating")
    ecosystem: Optional[str] = Field(None, description="Ecosystem / language")
    package: Optional[str] = Field(None, description="Affected package")
    source_url: Optional[str] = Field(None, description="Link to advisory")
    published_at: Optional[datetime] = Field(None, description="Publication timestamp")


class ServerCveResponse(BaseModel):
    server_id: str = Field(..., description="Server identifier")
    cves: List[CVEInfo] = Field(default_factory=list, description="List of CVEs affecting the server")


@router.get(
    "/servers/{server_id}/cves",
    response_model=ServerCveResponse,
    summary="Retrieve CVEs for a given server",
)
def get_server_cves(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerCveResponse:
    """
    Return all CVEs linked to the supplied `server_id`.
    """
    # Join advisory -> link and filter by server_id
    advisories = (
        db.query(VulnAdvisory)
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .filter(VulnLink.server_id == server_id)
        .all()
    )

    cve_list = [
        CVEInfo(
            id=adv.cve_id,
            feed=adv.feed,
            summary=adv.summary,
            severity=adv.severity,
            ecosystem=adv.ecosystem,
            package=adv.package,
            source_url=adv.source_url,
            published_at=adv.published_at,
        )
        for adv in advisories
    ]

    return ServerCveResponse(server_id=server_id, cves=cve_list)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.server_cve_search_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Build a throw‑away SQLite DB and override the session dependency
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables for the real models
    from app.models import Base  # Base is the declarative base used by the app

    Base.metadata.create_all(bind=engine)

    # Dependency override
    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed data
    with TestSessionLocal() as db:
        adv1 = VulnAdvisory(
            id=1,
            cve_id="CVE-2023-0001",
            feed="nvd",
            summary="Test vulnerability 1",
            severity="high",
            ecosystem="python",
            package="example-pkg",
            source_url="https://example.com/adv1",
            published_at=datetime(2023, 1, 1, 0, 0, 0),
        )
        adv2 = VulnAdvisory(
            id=2,
            cve_id="CVE-2023-0002",
            feed="github",
            summary="Test vulnerability 2",
            severity="medium",
            ecosystem="go",
            package="another-pkg",
            source_url="https://example.com/adv2",
            published_at=datetime(2023, 2, 2, 0, 0, 0),
        )
        db.add_all([adv1, adv2])
        db.flush()  # obtain primary keys

        link1 = VulnLink(
            id=1,
            server_id="srv-001",
            advisory_id=adv1.id,
        )
        link2 = VulnLink(
            id=2,
            server_id="srv-001",
            advisory_id=adv2.id,
        )
        db.add_all([link1, link2])
        db.commit()

    client = TestClient(app)

    resp = client.get("/api/servers/srv-001/cves")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data.get("server_id") != "srv-001":
        print("FAIL: server_id mismatch", file=sys.stderr)
        sys.exit(1)

    cves = data.get("cves", [])
    if not any(cve.get("id") == "CVE-2023-0001" for cve in cves):
        print("FAIL: expected CVE not found", file=sys.stderr)
        sys.exit(1)

    if len(cves) < 1:
        print("FAIL: no CVEs returned", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)