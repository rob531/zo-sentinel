from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api")

class CVESummary(BaseModel):
    id: int
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: datetime
    link_confidence: float

class ServerCVESummaryResponse(BaseModel):
    server_id: int
    cve_summaries: list[CVESummary]

@router.get("/server/{server_id}/cve_summary", response_model=ServerCVESummaryResponse)
def get_server_cve_summary(
    server_id: int,
    session: Session = Depends(get_session)
) -> ServerCVESummaryResponse:
    links = session.query(VulnLink).filter(VulnLink.server_id == server_id).all()
    
    if not links:
        return ServerCVESummaryResponse(server_id=server_id, cve_summaries=[])
    
    advisory_ids = [link.advisory_id for link in links]
    
    advisories = session.query(VulnAdvisory).filter(VulnAdvisory.id.in_(advisory_ids)).all()
    advisory_map = {adv.id: adv for adv in advisories}
    
    confidence_map: dict[int, list[float]] = {}
    for link in links:
        if link.advisory_id not in confidence_map:
            confidence_map[link.advisory_id] = []
        confidence_map[link.advisory_id].append(link.match_confidence)
    
    cve_summaries = []
    for adv_id, confs in confidence_map.items():
        adv = advisory_map[adv_id]
        avg_confidence = sum(confs) / len(confs)
        
        cve_summaries.append(CVESummary(
            id=adv.id,
            summary=adv.summary,
            severity=adv.severity,
            ecosystem=adv.ecosystem,
            package=adv.package,
            published_at=adv.published_at,
            link_confidence=avg_confidence
        ))
    
    cve_summaries.sort(key=lambda x: x.id)
    
    return ServerCVESummaryResponse(server_id=server_id, cve_summaries=cve_summaries)


def create_app() -> FastAPI:
    from app.main import app as main_app
    main_app.include_router(router)
    return main_app


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE vuln_advisories (
                id INTEGER PRIMARY KEY,
                summary TEXT,
                severity TEXT,
                ecosystem TEXT,
                package TEXT,
                published_at TEXT,
                feed TEXT,
                source_url TEXT,
                content_hash TEXT,
                aliases TEXT,
                identities TEXT,
                affected_ranges TEXT,
                fetched_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY,
                advisory_id INTEGER,
                server_id INTEGER,
                match_confidence REAL,
                match_value TEXT,
                match_basis TEXT,
                linked_at TEXT
            )
        """))
        conn.commit()
    
    SessionLocal = sessionmaker(bind=engine)
    
    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO vuln_advisories (id, summary, severity, ecosystem, package, published_at)
            VALUES 
                (101, 'CVE-2024-0001: Buffer overflow in libfoo', 'HIGH', 'PyPI', 'libfoo', '2024-01-15T00:00:00'),
                (102, 'CVE-2024-0002: SQL injection in libbar', 'CRITICAL', 'npm', 'libbar', '2024-02-20T00:00:00')
        """))
        session.execute(text("""
            INSERT INTO vuln_links (advisory_id, server_id, match_confidence, match_value, match_basis, linked_at)
            VALUES 
                (101, 1, 0.9, 'libfoo>=1.0', 'package_match', '2024-03-01'),
                (101, 1, 0.7, 'libfoo-1.2', 'version_match', '2024-03-01'),
                (102, 1, 1.0, 'libbar@2.0', 'exact_match', '2024-03-02')
        """))
        session.commit()
    
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    test_client = TestClient(app)
    response = test_client.get("/api/server/1/cve_summary")
    
    assert response.status_code == 200
    data = response.json()
    assert data["server_id"] == 1
    assert len(data["cve_summaries"]) == 2
    
    assert data["cve_summaries"][0]["id"] == 101
    assert data["cve_summaries"][0]["link_confidence"] == 0.8
    
    assert data["cve_summaries"][1]["id"] == 102
    assert data["cve_summaries"][1]["link_confidence"] == 1.0
    
    print("PASS")