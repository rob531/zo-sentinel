from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Optional, List

router = APIRouter()

class ServerImpact(BaseModel):
    name: str
    risk_tier: Optional[str]
    match_confidence: Optional[float]

class CVEServerImpactResponse(BaseModel):
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    summary: Optional[str]
    servers: List[ServerImpact]

@router.get("/api/cve/{cve_id}/servers", response_model=CVEServerImpactResponse)
def get_cve_server_impact(
    cve_id: str,
    package: Optional[str] = Query(None),
    session: Session = Depends(__import__("app.db", fromlist=["get_session"]).get_session)
):
    base_query = text("""
        SELECT DISTINCT
            va.severity,
            va.ecosystem,
            va.package,
            va.summary,
            msr.name,
            msr.risk_tier,
            vl.match_confidence
        FROM vuln_advisories va
        JOIN vuln_links vl ON va.id = vl.advisory_id
        JOIN McpServerRegistry msr ON vl.server_id = msr.server_id
        WHERE va.id = :cve_id
    """)
    params = {"cve_id": cve_id}
    if package:
        base_query = text("""
            SELECT DISTINCT
                va.severity,
                va.ecosystem,
                va.package,
                va.summary,
                msr.name,
                msr.risk_tier,
                vl.match_confidence
            FROM vuln_advisories va
            JOIN vuln_links vl ON va.id = vl.advisory_id
            JOIN McpServerRegistry msr ON vl.server_id = msr.server_id
            WHERE va.id = :cve_id AND va.package = :package
        """)
        params["package"] = package
    result = session.execute(base_query, params).fetchall()
    if not result:
        return CVEServerImpactResponse(
            severity=None, ecosystem=None, package=None, summary=None, servers=[]
        )
    row = result[0]
    servers = [
        ServerImpact(name=r.name, risk_tier=r.risk_tier, match_confidence=r.match_confidence)
        for r in result
    ]
    return CVEServerImpactResponse(
        severity=row.severity,
        ecosystem=row.ecosystem,
        package=row.package,
        summary=row.summary,
        servers=servers
    )

def get_exemption():
    pass

def health():
    pass

def build_search_index():
    pass

def signal_handler():
    pass

def cadence_summary():
    pass

def get_all_services_health():
    pass

def get_overall_health():
    pass

def ensure_tables():
    pass

def dashboard_stats():
    pass

def recent_cves():
    pass

def get_registry():
    pass

def get_server_by_name():
    pass

def get_contract_by_id():
    pass

def fetch_mcp_server_data():
    pass

def send_heartbeat():
    pass

def get_summary_statistics():
    pass

def compute_comparison_id():
    pass

def get_discrepancy_summary():
    pass

def get_unknown_risk_servers():
    pass

if __name__ == "__main__":
    import json
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from app.db import get_session

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id INTEGER PRIMARY KEY,
                name TEXT,
                risk_tier TEXT,
                confidence REAL,
                description TEXT,
                first_seen TEXT,
                last_assessed TEXT,
                last_scanned TEXT,
                last_seen TEXT,
                meta TEXT,
                registry_source TEXT,
                scan_count INTEGER,
                trust_score REAL,
                url TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_advisories (
                id TEXT PRIMARY KEY,
                affected_ranges TEXT,
                aliases TEXT,
                content_hash TEXT,
                ecosystem TEXT,
                feed TEXT,
                fetched_at TEXT,
                identities TEXT,
                package TEXT,
                published_at TEXT,
                severity TEXT,
                source_url TEXT,
                summary TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY,
                advisory_id TEXT,
                linked_at TEXT,
                match_basis TEXT,
                match_confidence REAL,
                match_value TEXT,
                server_id INTEGER
            )
        """))
        conn.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier) VALUES
            (1, 'server-alpha', 'high'),
            (2, 'server-beta', 'medium'),
            (3, 'server-gamma', 'low')
        """))
        conn.execute(text("""
            INSERT INTO vuln_advisories (id, severity, ecosystem, package, summary) VALUES
            ('CVE-2024-0001', 'HIGH', 'npm', 'test-package', 'Test advisory one'),
            ('CVE-2024-0002', 'CRITICAL', 'pip', 'other-package', 'Test advisory two')
        """))
        conn.execute(text("""
            INSERT INTO vuln_links (advisory_id, server_id, match_confidence) VALUES
            ('CVE-2024-0001', 1, 0.95),
            ('CVE-2024-0001', 2, 0.85),
            ('CVE-2024-0002', 3, 0.90)
        """))

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app) as client:
        response = client.get("/api/cve/CVE-2024-0001/servers")
        assert response.status_code == 200
        data = response.json()
        assert len(data.get("servers", [])) >= 1
        assert data.get("severity") is not None
        assert any(s.get("risk_tier") for s in data.get("servers", []))

    print("PASS")
    exit(0)