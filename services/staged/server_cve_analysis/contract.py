from typing import List
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, VulnLink, VulnAdvisory


router = APIRouter()


class CVEModel(BaseModel):
    id: str
    severity: str
    published_at: str
    match_basis: str
    match_value: str


class ServerCVEsResponse(BaseModel):
    server_id: int
    cves: List[CVEModel]


def get_server_cves(server_id: int, session=None):
    if session is None:
        session = get_session()
    query = text("""
        SELECT va.id, va.severity, va.published_at, vl.match_basis, vl.match_value
        FROM McpServerRegistry msr
        JOIN vuln_links vl ON msr.server_id = vl.server_id
        JOIN vuln_advisories va ON vl.advisory_id = va.id
        WHERE msr.server_id = :server_id
        ORDER BY va.published_at DESC
    """)
    result = session.execute(query, {"server_id": server_id})
    rows = result.fetchall()
    return rows


@router.get("/servers/{server_id}/cves", response_model=ServerCVEsResponse)
def get_cves_for_server(server_id: int, session=Depends(get_session)):
    rows = get_server_cves(server_id, session)
    cves = [
        CVEModel(
            id=row[0],
            severity=row[1],
            published_at=str(row[2]),
            match_basis=row[3],
            match_value=row[4]
        )
        for row in rows
    ]
    return ServerCVEsResponse(server_id=server_id, cves=cves)


def create_app():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")

    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(bind=in_memory_engine)

    with in_memory_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id INTEGER PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                description TEXT,
                risk_tier TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                trust_score REAL,
                confidence REAL,
                scan_count INTEGER,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_advisories (
                id TEXT PRIMARY KEY,
                severity TEXT,
                published_at TEXT,
                summary TEXT,
                source_url TEXT,
                ecosystem TEXT,
                package TEXT,
                feed TEXT,
                aliases TEXT,
                affected_ranges TEXT,
                content_hash TEXT,
                identities TEXT,
                fetched_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                advisory_id TEXT,
                match_basis TEXT,
                match_confidence REAL,
                match_value TEXT,
                linked_at TEXT
            )
        """))
        conn.commit()

    session = TestingSessionLocal()
    servers = [
        (1, "server-alpha", "CVE-2024-1001", "HIGH", "2024-01-15", "exact_match", "libssl=1.1.0"),
        (2, "server-beta", "CVE-2024-1002", "MEDIUM", "2024-02-20", "package_name", "openssl"),
        (3, "server-gamma", "CVE-2024-1003", "CRITICAL", "2024-03-10", "exact_match", "kernel=5.4.0"),
        (4, "server-delta", "CVE-2024-1004", "LOW", "2024-04-05", "fuzzy_match", "curl"),
        (5, "server-epsilon", "CVE-2024-1005", "HIGH", "2024-05-12", "exact_match", "nginx"),
    ]
    for sid, name, cve_id, sev, pub, basis, val in servers:
        session.execute(text("INSERT INTO McpServerRegistry (server_id, name) VALUES (:sid, :name)"),
                       {"sid": sid, "name": name})
        session.execute(text("""
            INSERT INTO vuln_advisories (id, severity, published_at, summary)
            VALUES (:id, :sev, :pub, :pub || '-summary')
        """), {"id": cve_id, "sev": sev, "pub": pub})
        session.execute(text("""
            INSERT INTO vuln_links (server_id, advisory_id, match_basis, match_value)
            VALUES (:sid, :aid, :basis, :val)
        """), {"sid": sid, "aid": cve_id, "basis": basis, "val": val})
    session.commit()
    session.close()

    that_app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    with TestClient(that_app) as client:
        response = client.get("/api/servers/1/cves")
        assert response.status_code == 200
        data = response.json()
        assert data["server_id"] == 1
        assert len(data["cves"]) == 1
        assert data["cves"][0]["id"] == "CVE-2024-1001"
        assert data["cves"][0]["severity"] == "HIGH"

        response = client.get("/api/servers/3/cves")
        assert response.status_code == 200
        data = response.json()
        assert data["server_id"] == 3
        assert len(data["cves"]) == 1
        assert data["cves"][0]["id"] == "CVE-2024-1003"
        assert data["cves"][0]["severity"] == "CRITICAL"

        response = client.get("/api/servers/999/cves")
        assert response.status_code == 200
        data = response.json()
        assert data["server_id"] == 999
        assert len(data["cves"]) == 0

    print("PASS")