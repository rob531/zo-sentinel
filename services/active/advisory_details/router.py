# deps: fastapi, sqlalchemy, pydantic
"""Advisory Details Service - fetch vulnerability advisory details and linked servers."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["advisory_details"])


class AdvisorySummary(BaseModel):
    id: str
    feed: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    source_url: str
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LinkedServer(BaseModel):
    server_id: str
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdvisoryDetail(BaseModel):
    id: str
    feed: str
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    affected_ranges: Optional[dict] = None
    aliases: Optional[dict] = None
    identities: Optional[dict] = None
    source_url: str
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdvisoryWithLinks(BaseModel):
    advisory: AdvisoryDetail
    linked_servers: list[LinkedServer]
    total_links: int


@router.get("/advisory_details/summary/{advisory_id}", response_model=AdvisorySummary)
def get_advisory_summary(advisory_id: str, db: Session = Depends(get_session)):
    """Return summary info for a single advisory (no linked servers)."""
    advisory = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if not advisory:
        raise HTTPException(status_code=404, detail=f"Advisory {advisory_id} not found")
    return advisory


@router.get("/advisory_details/{advisory_id}", response_model=AdvisoryWithLinks)
def get_advisory_detail(advisory_id: str, db: Session = Depends(get_session)):
    """Return full advisory details with all linked servers."""
    advisory = db.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if not advisory:
        raise HTTPException(status_code=404, detail=f"Advisory {advisory_id} not found")

    links = db.query(VulnLink).filter(VulnLink.advisory_id == advisory_id).all()
    return AdvisoryWithLinks(
        advisory=AdvisoryDetail.model_validate(advisory),
        linked_servers=[LinkedServer.model_validate(l) for l in links],
        total_links=len(links),
    )


@router.get("/advisory_details", response_model=list[AdvisorySummary])
def list_advisories(
    feed: Optional[str] = Query(None, description="Filter by feed: osv, ghsa, nvd"),
    severity: Optional[str] = Query(None, description="Filter by severity: CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN"),
    ecosystem: Optional[str] = Query(None, description="Filter by ecosystem: npm, PyPI, GitHub, etc."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
):
    """List advisories with optional filters, paginated."""
    q = db.query(VulnAdvisory)
    if feed:
        q = q.filter(VulnAdvisory.feed == feed)
    if severity:
        q = q.filter(VulnAdvisory.severity == severity)
    if ecosystem:
        q = q.filter(VulnAdvisory.ecosystem == ecosystem)
    return q.order_by(VulnAdvisory.fetched_at.desc()).offset(offset).limit(limit).all()


# Self-test
if __name__ == "__main__":
    import sqlalchemy
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)

    Session = sessionmaker(bind=engine)
    sess = Session()

    # Seed test data
    adv = VulnAdvisory(
        id="CVE-2025-1234",
        feed="nvd",
        summary="Test vulnerability",
        severity="HIGH",
        ecosystem="npm",
        package="test-package",
        source_url="https://nvd.nist.gov/vuln/detail/CVE-2025-1234",
        published_at=datetime(2025, 1, 1),
        fetched_at=datetime.now(),
    )
    sess.add(adv)
    sess.commit()

    # Test list
    from app.db import get_session as _get_session
    from app import db as _db_module

    _db_module._session_factory = sessionmaker(bind=engine)

    # Override dependency
    from app.db import get_session
    from fastapi import FastAPI

    app = FastAPI()

    def _override_get_session():
        return sess

    app.dependency_overrides[get_session] = _override_get_session

    @app.get("/test")
    def _test():
        return list_advisories(db=sess)

    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "CVE-2025-1234"
    print("Self-test passed: advisory_details router")
