from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter(prefix="/api", tags=["vuln"])


class VulnAdvisoryResponse(BaseModel):
    id: str
    summary: str
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    affected_ranges: Optional[str] = None
    aliases: Optional[str] = None
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("/vuln/advisories/{advisory_id}", response_model=VulnAdvisoryResponse)
async def get_vuln_advisories_by_id(
    advisory_id: str,
    session=Depends(get_session),
):
    advisory = session.query(VulnAdvisory).filter(VulnAdvisory.id == advisory_id).first()
    if not advisory:
        raise HTTPException(status_code=404, detail="Advisory not found")
    return advisory


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE vuln_advisories (
                affected_ranges TEXT,
                aliases TEXT,
                content_hash TEXT,
                ecosystem TEXT,
                feed TEXT,
                fetched_at TIMESTAMP,
                id TEXT PRIMARY KEY,
                identities TEXT,
                package TEXT,
                published_at TIMESTAMP,
                severity TEXT,
                source_url TEXT,
                summary TEXT
            )
        """))

    SessionLocal = sessionmaker(bind=engine)

    def override_get_session() -> Session:
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as session:
        session.add(VulnAdvisory(
            id="GHSA-1234-abcd-5678",
            summary="Test vulnerability advisory",
            severity="HIGH",
            ecosystem="npm",
            package="test-package",
            affected_ranges="<1.0.0",
            aliases="CVE-2024-1234",
            source_url="https://example.com/advisory",
            published_at=datetime(2024, 1, 15, 10, 0, 0),
        ))
        session.commit()

    client = TestClient(app)

    response = client.get("/api/vuln/advisories/GHSA-1234-abcd-5678")
    data = response.json()

    assert response.status_code == 200
    assert data["id"] == "GHSA-1234-abcd-5678"
    assert data["summary"] == "Test vulnerability advisory"
    assert data["severity"] == "HIGH"
    assert data["ecosystem"] == "npm"
    assert data["package"] == "test-package"

    not_found = client.get("/api/vuln/advisories/nonexistent-id")
    assert not_found.status_code == 404

    print("PASS")