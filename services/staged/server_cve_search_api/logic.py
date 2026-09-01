from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session

router = APIRouter()


class CVEItem(BaseModel):
    cve_id: str
    severity: Optional[str] = None
    description: Optional[str] = None
    cvss_score: Optional[float] = None


class ServerCVEsResponse(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    cves: List[CVEItem]


def get_server_cves(server_id: int, session: Session = Depends(get_session)) -> ServerCVEsResponse:
    """
    Get CVEs associated with a server by joining McpServerRegistry with VulnLink.
    Returns server info and list of associated CVEs.
    """
    # Verify server exists and get server info
    server_query = text("""
        SELECT server_id, server_name 
        FROM McpServerRegistry 
        WHERE server_id = :server_id
    """)
    server_result = session.execute(server_query, {"server_id": server_id}).fetchone()
    
    if not server_result:
        raise ValueError(f"Server with id {server_id} not found")
    
    server_name = server_result[1] if len(server_result) > 1 else None
    
    # Get CVEs via VulnLink join
    cve_query = text("""
        SELECT DISTINCT v.cve_id, v.severity, v.description, v.cvss_score
        FROM vuln_link vl
        JOIN vuln_advisory v ON vl.cve_id = v.cve_id
        WHERE vl.server_id = :server_id
        ORDER BY v.cvss_score DESC NULLS LAST, v.cve_id
    """)
    cve_results = session.execute(cve_query, {"server_id": server_id}).fetchall()
    
    cves = [
        CVEItem(
            cve_id=row[0],
            severity=row[1],
            description=row[2],
            cvss_score=row[3]
        )
        for row in cve_results
    ]
    
    return ServerCVEsResponse(
        server_id=str(server_id),
        server_name=server_name,
        cves=cves
    )


@router.get("/api/servers/{server_id}/cves", response_model=ServerCVEsResponse)
def get_cves_for_server(
    server_id: int,
    session: Session = Depends(get_session)
) -> ServerCVEsResponse:
    """
    GET /api/servers/{server_id}/cves
    Returns CVEs associated with the specified server.
    """
    try:
        return get_server_cves(server_id, session)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import sqlite3

    # Create in-memory SQLite for self-test
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    
    # Create tables matching Postgres schema
    conn.execute("""
        CREATE TABLE McpServerRegistry (
            server_id INTEGER PRIMARY KEY,
            server_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.execute("""
        CREATE TABLE vuln_advisory (
            cve_id TEXT PRIMARY KEY,
            severity TEXT,
            description TEXT,
            cvss_score REAL
        )
    """)
    
    conn.execute("""
        CREATE TABLE vuln_link (
            link_id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            cve_id TEXT,
            FOREIGN KEY (server_id) REFERENCES McpServerRegistry(server_id),
            FOREIGN KEY (cve_id) REFERENCES vuln_advisory(cve_id)
        )
    """)
    
    # Seed test data
    conn.execute("INSERT INTO McpServerRegistry (server_id, server_name) VALUES (1, 'test-server-1')")
    conn.execute("INSERT INTO McpServerRegistry (server_id, server_name) VALUES (2, 'test-server-2')")
    
    conn.execute("INSERT INTO vuln_advisory (cve_id, severity, description, cvss_score) VALUES ('CVE-2021-44228', 'CRITICAL', 'Log4Shell vulnerability', 10.0)")
    conn.execute("INSERT INTO vuln_advisory (cve_id, severity, description, cvss_score) VALUES ('CVE-2022-12345', 'HIGH', 'Test vulnerability', 7.5)")
    conn.execute("INSERT INTO vuln_advisory (cve_id, severity, description, cvss_score) VALUES ('CVE-2023-99999', 'MEDIUM', 'Another test', 5.0)")
    
    conn.execute("INSERT INTO vuln_link (server_id, cve_id) VALUES (1, 'CVE-2021-44228')")
    conn.execute("INSERT INTO vuln_link (server_id, cve_id) VALUES (1, 'CVE-2022-12345')")
    conn.execute("INSERT INTO vuln_link (server_id, cve_id) VALUES (2, 'CVE-2023-99999')")
    conn.commit()
    
    # Create SQLAlchemy engine for in-memory SQLite
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"isolation_level": None}
    )
    
    # Use the existing connection
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool
    )
    
    # Recreate with proper connection handling
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Use raw connection approach for SQLite
    from sqlalchemy import event
    
    # Create tables in test engine
    from sqlalchemy.schema import CreateTable
    from app.models import McpServerRegistry, VulnAdvisory
    
    # Simplified approach - create minimal tables via raw SQL
    with test_engine.connect() as test_conn:
        test_conn.execute(text("PRAGMA foreign_keys = ON"))
        test_conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                server_id INTEGER PRIMARY KEY,
                server_name TEXT
            )
        """))
        test_conn.execute(text("""
            CREATE TABLE IF NOT EXISTS vuln_advisory (
                cve_id TEXT PRIMARY KEY,
                severity TEXT,
                description TEXT,
                cvss_score REAL
            )
        """))
        test_conn.execute(text("""
            CREATE TABLE IF NOT EXISTS vuln_link (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER,
                cve_id TEXT
            )
        """))
        test_conn.commit()
        
        # Seed data
        test_conn.execute(text("INSERT INTO McpServerRegistry VALUES (1, 'test-server-1')"))
        test_conn.execute(text("INSERT INTO McpServerRegistry VALUES (2, 'test-server-2')"))
        test_conn.execute(text("INSERT INTO vuln_advisory VALUES ('CVE-2021-44228', 'CRITICAL', 'Log4Shell', 10.0)"))
        test_conn.execute(text("INSERT INTO vuln_advisory VALUES ('CVE-2022-12345', 'HIGH', 'Test vuln', 7.5)"))
        test_conn.execute(text("INSERT INTO vuln_link VALUES (NULL, 1, 'CVE-2021-44228')"))
        test_conn.execute(text("INSERT INTO vuln_link VALUES (NULL, 1, 'CVE-2022-12345')"))
        test_conn.commit()
    
    TestSession = sessionmaker(bind=test_engine)
    
    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()
    
    # Create FastAPI app for testing
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(test_app)
    
    response = client.get("/api/servers/1/cves")
    
    if response.status_code == 200:
        data = response.json()
        if data.get("cves") and len(data["cves"]) > 0:
            print(f"PASS - Found {len(data['cves'])} CVEs for server 1")
            print(f"CVEs: {[c['cve_id'] for c in data['cves']]}")
        else:
            print("FAIL - No CVEs returned")
    else:
        print(f"FAIL - Status {response.status_code}: {response.text}")