"""
services/staged/server_cve_exposure/contract.py

FastAPI contract for the `server_cve_exposure` staged service.
Provides a GET endpoint that returns CVE exposure details for a given server.
"""

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

# ----------------------------------------------------------------------
# Real data layer imports (must remain unchanged for production)
# ----------------------------------------------------------------------
from app.db import get_session, Base  # get_session is the production dependency
from app.models import (
    McpServerRegistry,
    VulnLink,
    VulnAdvisory,
)  # noqa: F401

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class AdvisoryInfo(BaseModel):
    id: int
    summary: str
    severity: str
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None


class ServerCVEExposureResponse(BaseModel):
    server_id: int
    server_name: str
    total_cves: int
    critical_count: int
    high_count: int
    medium_count: int
    advisories: List[AdvisoryInfo] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/cve-exposure",
    response_model=ServerCVEExposureResponse,
    name="get_server_cve_exposure",
)
def get_server_cve_exposure(
    server_id: int, session: Session = Depends(get_session)
) -> ServerCVEExposureResponse:
    """
    Retrieve CVE exposure information for a specific server.
    """
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Join links to advisories
    rows = (
        session.query(VulnLink, VulnAdvisory)
        .join(
            VulnAdvisory,
            VulnLink.advisory_id == VulnAdvisory.id,
        )
        .filter(VulnLink.server_id == server_id)
        .all()
    )

    advisories: List[AdvisoryInfo] = []
    severity_counts = {"critical": 0, "high": 0, "medium": 0}
    for link, adv in rows:
        advisories.append(
            AdvisoryInfo(
                id=adv.id,
                summary=adv.summary,
                severity=adv.severity,
                ecosystem=getattr(adv, "ecosystem", None),
                package=getattr(adv, "package", None),
                source_url=getattr(adv, "source_url", None),
                published_at=getattr(adv, "published_at", None),
            )
        )
        sev = adv.severity.lower()
        if sev in severity_counts:
            severity_counts[sev] += 1

    total_cves = len(advisories)

    return ServerCVEExposureResponse(
        server_id=server.id,
        server_name=getattr(server, "name", f"server-{server.id}"),
        total_cves=total_cves,
        critical_count=severity_counts["critical"],
        high_count=severity_counts["high"],
        medium_count=severity_counts["medium"],
        advisories=advisories,
    )


# ----------------------------------------------------------------------
# FastAPI app (used by the test client)
# ----------------------------------------------------------------------
app = FastAPI()
app.include_router(router)


# ----------------------------------------------------------------------
# Self‑test (executed with `python -m services.staged.server_cve_exposure.contract`)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------
    # Create an in‑memory SQLite DB and override the production dependency
    # ------------------------------------------------------------------
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables for the imported models
    Base.metadata.create_all(engine)

    # Dependency override
    def get_test_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Seed test data
    # ------------------------------------------------------------------
    with SessionLocal() as db:
        # Servers
        srv1 = McpServerRegistry(id=1, name="alpha")
        srv2 = McpServerRegistry(id=2, name="beta")
        db.add_all([srv1, srv2])

        # Advisories
        adv1 = VulnAdvisory(
            id=101,
            summary="Critical issue",
            severity="critical",
            ecosystem="python",
            package="pkgA",
            source_url="http://example.com/adv1",
            published_at=datetime(2023, 1, 1, 12, 0, 0),
        )
        adv2 = VulnAdvisory(
            id=102,
            summary="High severity issue",
            severity="high",
            ecosystem="nodejs",
            package="pkgB",
            source_url="http://example.com/adv2",
            published_at=datetime(2023, 2, 1, 12, 0, 0),
        )
        adv3 = VulnAdvisory(
            id=103,
            summary="Medium severity issue",
            severity="medium",
            ecosystem="go",
            package="pkgC",
            source_url="http://example.com/adv3",
            published_at=datetime(2023, 3, 1, 12, 0, 0),
        )
        db.add_all([adv1, adv2, adv3])

        # Links
        link1 = VulnLink(server_id=1, advisory_id=101)
        link2 = VulnLink(server_id=1, advisory_id=102)
        link3 = VulnLink(server_id=2, advisory_id=103)
        db.add_all([link1, link2, link3])

        db.commit()

    # ------------------------------------------------------------------
    # Run acceptance tests
    # ------------------------------------------------------------------
    client = TestClient(app)

    for server_id in (1, 2):
        resp = client.get(f"/api/servers/{server_id}/cve-exposure")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert data["server_id"] == server_id
        assert data["total_cves"] >= 0
        assert isinstance(data["critical_count"], int) and data["critical_count"] >= 0
        assert isinstance(data["high_count"], int) and data["high_count"] >= 0
        assert isinstance(data["medium_count"], int) and data["medium_count"] >= 0

    print("PASS")
    sys.exit(0)