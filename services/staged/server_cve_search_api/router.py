from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from app.db import get_session
from app.models import VulnAdvisory, VulnLink, Base
from sqlalchemy.orm import Session
from sqlalchemy import desc

router = APIRouter(prefix="/api", tags=["servers"])


class CVESummary(BaseModel):
    advisory_id: int
    severity: str
    summary: str
    published_at: str
    source_url: str

    class Config:
        from_attributes = True


class ServerCVEsResponse(BaseModel):
    server_id: str
    cves: List[CVESummary]


@router.get("/servers/{server_id}/cves", response_model=ServerCVEsResponse)
def get_server_cves(server_id: str, session: Session = Depends(get_session)):
    results = (
        session.query(VulnAdvisory, VulnLink)
        .join(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .filter(VulnLink.server_id == server_id)
        .order_by(desc(VulnAdvisory.severity), desc(VulnAdvisory.published_at))
        .all()
    )
    cves = [
        CVESummary(
            advisory_id=advisory.id,
            severity=advisory.severity,
            summary=advisory.summary,
            published_at=str(advisory.published_at),
            source_url=advisory.source_url,
        )
        for advisory, _ in results
    ]
    return ServerCVEsResponse(server_id=server_id, cves=cves)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from datetime import datetime

    db = TestingSessionLocal()
    db.query(VulnAdvisory).delete()
    db.query(VulnLink).delete()
    db.commit()

    adv1 = VulnAdvisory(
        id=1,
        severity="CRITICAL",
        summary="Remote code execution via buffer overflow",
        published_at=datetime(2024, 1, 15),
        source_url="https://example.com/cve-2024-0001",
        affected_ranges="*",
        aliases="CVE-2024-0001",
        content_hash="abc",
        ecosystem="pypi",
        feed="nvd",
        fetched_at=datetime.now(),
        identities="[]",
        package="libexample",
    )
    adv2 = VulnAdvisory(
        id=2,
        severity="HIGH",
        summary="SQL injection in query handler",
        published_at=datetime(2024, 2, 10),
        source_url="https://example.com/cve-2024-0002",
        affected_ranges="*",
        aliases="CVE-2024-0002",
        content_hash="def",
        ecosystem="pypi",
        feed="nvd",
        fetched_at=datetime.now(),
        identities="[]",
        package="libexample",
    )
    adv3 = VulnAdvisory(
        id=3,
        severity="MEDIUM",
        summary="Information disclosure via misconfigured ACL",
        published_at=datetime(2024, 3, 5),
        source_url="https://example.com/cve-2024-0003",
        affected_ranges="*",
        aliases="CVE-2024-0003",
        content_hash="ghi",
        ecosystem="pypi",
        feed="nvd",
        fetched_at=datetime.now(),
        identities="[]",
        package="libexample",
    )
    adv4 = VulnAdvisory(
        id=4,
        severity="CRITICAL",
        summary="Privilege escalation via insecure default",
        published_at=datetime(2024, 1, 20),
        source_url="https://example.com/cve-2024-0004",
        affected_ranges="*",
        aliases="CVE-2024-0004",
        content_hash="jkl",
        ecosystem="pypi",
        feed="nvd",
        fetched_at=datetime.now(),
        identities="[]",
        package="libexample",
    )
    db.add_all([adv1, adv2, adv3, adv4])
    db.commit()

    link1 = VulnLink(
        advisory_id=1, server_id="Y", linked_at=datetime.now(),
        match_basis="package", match_confidence=0.95, match_value="libexample"
    )
    link2 = VulnLink(
        advisory_id=2, server_id="Y", linked_at=datetime.now(),
        match_basis="package", match_confidence=0.90, match_value="libexample"
    )
    link3 = VulnLink(
        advisory_id=3, server_id="Y", linked_at=datetime.now(),
        match_basis="package", match_confidence=0.85, match_value="libexample"
    )
    link4 = VulnLink(
        advisory_id=4, server_id="Y", linked_at=datetime.now(),
        match_basis="package", match_confidence=0.92, match_value="libexample"
    )
    db.add_all([link1, link2, link3, link4])
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = app.test_client()
    response = client.get("/api/servers/Y/cves")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["cves"]) == 4, f"Expected 4 cves, got {len(data['cves'])}"
    assert data["cves"][0]["severity"] == "CRITICAL", \
        f"Expected first CVE severity CRITICAL, got {data['cves'][0]['severity']}"
    print("PASS")