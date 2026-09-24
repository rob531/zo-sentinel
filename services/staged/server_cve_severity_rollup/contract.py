from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.db import get_session
from app.models import McpServerRegistry, VulnLink, VulnAdvisory


app = FastAPI()


class ServerSeverityItem(BaseModel):
    server_id: int
    name: str
    severity: Optional[str]
    summary: Optional[str]


class CVESeverityRollupResponse(BaseModel):
    severity_counts: dict
    critical_servers: list
    high_servers: list


@app.get("/api/servers/cve-severity-rollup", response_model=CVESeverityRollupResponse)
def get_cve_severity_rollup(session: Session = Depends(get_session)):
    query = text("""
        SELECT 
            sr.server_id,
            sr.name,
            va.severity,
            va.summary
        FROM mcp_server_registry sr
        LEFT JOIN vuln_links vl ON sr.server_id = vl.server_id
        LEFT JOIN vuln_advisories va ON vl.advisory_id = va.id
        ORDER BY sr.server_id, va.severity
    """)
    result = session.execute(query)
    rows = result.fetchall()

    server_data = {}
    for row in rows:
        server_id, name, severity, summary = row
        if server_id not in server_data:
            server_data[server_id] = {
                "server_id": server_id,
                "name": name,
                "severity": severity,
                "summary": summary
            }
        elif severity is not None:
            current = server_data[server_id]["severity"]
            severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1, None: 0}
            if severity_order.get(severity, -1) > severity_order.get(current, -1):
                server_data[server_id]["severity"] = severity
                server_data[server_id]["summary"] = summary

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "none": 0}
    critical_servers = []
    high_servers = []

    for server in server_data.values():
        sev = server["severity"]
        if sev == "critical":
            severity_counts["critical"] += 1
            critical_servers.append(server)
        elif sev == "high":
            severity_counts["high"] += 1
            high_servers.append(server)
        elif sev == "medium":
            severity_counts["medium"] += 1
        elif sev == "low":
            severity_counts["low"] += 1
        else:
            severity_counts["none"] += 1

    return CVESeverityRollupResponse(
        severity_counts=severity_counts,
        critical_servers=critical_servers,
        high_servers=high_servers
    )


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT,
                registry_source TEXT,
                description TEXT,
                trust_score REAL,
                risk_tier TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
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
                id INTEGER PRIMARY KEY,
                severity TEXT,
                summary TEXT,
                aliases TEXT,
                affected_ranges TEXT,
                content_hash TEXT,
                ecosystem TEXT,
                feed TEXT,
                fetched_at TEXT,
                identities TEXT,
                package TEXT,
                published_at TEXT,
                source_url TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                advisory_id INTEGER,
                match_value TEXT,
                match_basis TEXT,
                match_confidence REAL,
                linked_at TEXT
            )
        """))

        for i in range(1, 5):
            conn.execute(text("""
                INSERT INTO mcp_server_registry (server_id, name, url, registry_source)
                VALUES (:sid, :name, :url, 'test')
            """), {"sid": i, "name": f"test-server-{i}", "url": f"http://test{i}.example.com"})

        conn.execute(text("""
            INSERT INTO vuln_advisories (id, severity, summary) VALUES (1, 'critical', 'Critical severity advisory')
        """))
        conn.execute(text("""
            INSERT INTO vuln_advisories (id, severity, summary) VALUES (2, 'high', 'High severity advisory')
        """))
        conn.execute(text("""
            INSERT INTO vuln_advisories (id, severity, summary) VALUES (3, 'medium', 'Medium severity advisory')
        """))

        conn.execute(text("""
            INSERT INTO vuln_links (server_id, advisory_id) VALUES (1, 1)
        """))
        conn.execute(text("""
            INSERT INTO vuln_links (server_id, advisory_id) VALUES (2, 2)
        """))
        conn.execute(text("""
            INSERT INTO vuln_links (server_id, advisory_id) VALUES (3, 3)
        """))

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(app.router)

    client = TestClient(test_app)
    test_app.dependency_overrides[get_session] = override_get_session

    response = client.get("/api/servers/cve-severity-rollup")
    assert response.status_code == 200
    data = response.json()

    assert set(data["severity_counts"].keys()) == {"critical", "high", "medium", "low", "none"}
    assert data["severity_counts"]["critical"] >= 1
    assert len(data["critical_servers"]) >= 1

    print("PASS")
    sys.exit(0)