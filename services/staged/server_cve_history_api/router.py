from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import Table, Column, Integer, String, DateTime, Text
from sqlalchemy.schema import MetaData
from datetime import datetime

from app.db import get_session
from app.models import VulnLink, VulnAdvisory

router = APIRouter()


class CVEHistoryItem(BaseModel):
    cve_id: str
    severity: str
    summary: str
    published_at: datetime

    class Config:
        from_attributes = True


@router.get("/api/servers/{server_id}/cve-history", response_model=list[CVEHistoryItem])
def get_cve_history(server_id: int, session: Session = Depends(get_session)):
    stmt = (
        select(VulnLink.advisory_id, VulnAdvisory.severity, VulnAdvisory.summary, VulnAdvisory.published_at)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .where(VulnLink.server_id == server_id)
        .order_by(VulnLink.linked_at)
    )
    result = session.execute(stmt).fetchall()
    return [
        CVEHistoryItem(
            cve_id=str(row[0]),
            severity=row[1] or "",
            summary=row[2] or "",
            published_at=row[3]
        )
        for row in result
    ]


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()

    vuln_advisories = Table(
        "vuln_advisories", metadata,
        Column("id", Integer, primary_key=True),
        Column("severity", String),
        Column("summary", String),
        Column("published_at", DateTime),
        Column("feed", String),
        Column("source_url", String),
        Column("fetched_at", DateTime),
        Column("aliases", Text),
        Column("affected_ranges", Text),
        Column("ecosystem", String),
        Column("package", String),
        Column("content_hash", String),
        Column("identities", Text),
    )

    vuln_links = Table(
        "vuln_links", metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", Integer),
        Column("advisory_id", Integer),
        Column("linked_at", DateTime),
        Column("match_value", String),
        Column("match_basis", String),
        Column("match_confidence", Integer),
    )

    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(vuln_advisories.insert().values([
            {"id": 1, "severity": "CRITICAL", "summary": "Remote code execution", "published_at": datetime(2024, 1, 1), "feed": "ghsa", "source_url": "https://example.com/1", "fetched_at": datetime(2024, 1, 2), "aliases": "CVE-2024-0001", "affected_ranges": "[]", "ecosystem": "pip", "package": "foo", "content_hash": "abc", "identities": "[]"},
            {"id": 2, "severity": "HIGH", "summary": "Privilege escalation", "published_at": datetime(2024, 2, 1), "feed": "ghsa", "source_url": "https://example.com/2", "fetched_at": datetime(2024, 2, 2), "aliases": "CVE-2024-0002", "affected_ranges": "[]", "ecosystem": "pip", "package": "bar", "content_hash": "def", "identities": "[]"},
            {"id": 3, "severity": "MEDIUM", "summary": "Information disclosure", "published_at": datetime(2024, 3, 1), "feed": "ghsa", "source_url": "https://example.com/3", "fetched_at": datetime(2024, 3, 2), "aliases": "CVE-2024-0003", "affected_ranges": "[]", "ecosystem": "pip", "package": "baz", "content_hash": "ghi", "identities": "[]"},
        ]))
        conn.execute(vuln_links.insert().values([
            {"id": 1, "server_id": 1, "advisory_id": 1, "linked_at": datetime(2024, 1, 10), "match_value": "foo", "match_basis": "package", "match_confidence": 95},
            {"id": 2, "server_id": 1, "advisory_id": 2, "linked_at": datetime(2024, 2, 15), "match_value": "bar", "match_basis": "package", "match_confidence": 90},
        ]))

    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        with session_factory() as session:
            yield session

    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        resp = client.get("/api/servers/1/cve-history")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "severity" in data[0]
    print("PASS")