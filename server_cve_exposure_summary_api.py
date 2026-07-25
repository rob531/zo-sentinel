from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.db import get_session
from app.models import Base, McpServerRegistry, VulnLinks, VulnAdvisories

router = APIRouter()


class Advisory(BaseModel):
    id: str = Field(..., alias="id")
    summary: str
    severity: str
    ecosystem: Optional[str] = None
    published_at: Optional[datetime] = None
    source_url: Optional[str] = None

    class Config:
        orm_mode = True
        allow_population_by_field_name = True


class CveExposureResponse(BaseModel):
    server_id: str
    server_name: str
    risk_tier: Optional[str] = None
    cve_count: int
    critical_count: int
    high_count: int
    medium_count: int
    advisories: List[Advisory] = []


@router.get(
    "/servers/{server_id}/cve-exposure",
    response_model=CveExposureResponse,
    responses={404: {"description": "Server not found"}},
)
def get_cve_exposure(
    server_id: str, db: Session = Depends(get_session)
) -> CveExposureResponse:
    # Fetch server metadata
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Fetch linked advisories
    advisories = (
        db.query(VulnAdvisories)
        .join(VulnLinks, VulnAdvisories.id == VulnLinks.advisory_id)
        .filter(VulnLinks.server_id == server_id)
        .options(joinedload(VulnAdvisories))
        .all()
    )

    # Compute severity counts
    critical = sum(1 for a in advisories if (a.severity or "").lower() == "critical")
    high = sum(1 for a in advisories if (a.severity or "").lower() == "high")
    medium = sum(1 for a in advisories if (a.severity or "").lower() == "medium")
    total = len(advisories)

    return CveExposureResponse(
        server_id=server.server_id,
        server_name=server.name,
        risk_tier=server.risk_tier,
        cve_count=total,
        critical_count=critical,
        high_count=high,
        medium_count=medium,
        advisories=[Advisory.from_orm(a) for a in advisories],
    )


if __name__ == "__main__":
    # ----------------------------------------------------------------------
    # Self‑test using an in‑memory SQLite database.
    # ----------------------------------------------------------------------
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create in‑memory SQLite engine and bind the metadata
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Dependency override to use the in‑memory session
    def get_test_session() -> Session:
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Populate test data
    with SessionLocal() as db:
        srv = McpServerRegistry(
            server_id="srv123", name="Test Server", risk_tier="high"
        )
        adv = VulnAdvisories(
            id="adv1",
            summary="Test CVE advisory",
            severity="critical",
            ecosystem="linux",
            published_at=datetime.utcnow(),
            source_url="http://example.com/adv1",
        )
        link = VulnLinks(server_id="srv123", advisory_id="adv1", match_confidence=0.95)

        db.add_all([srv, adv, link])
        db.commit()

    client = TestClient(app)

    # Successful request
    resp = client.get("/servers/srv123/cve-exposure")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == "srv123"
    assert data["cve_count"] >= 0
    assert isinstance(data["advisories"], list)

    # Not‑found request
    resp_nf = client.get("/servers/unknown/cve-exposure")
    assert resp_nf.status_code == 404, "Expected 404 for unknown server"

    print("PASS")