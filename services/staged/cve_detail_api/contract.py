from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


class LinkedServer(BaseModel):
    server_id: str
    server_name: str
    match_confidence: float
    linked_at: datetime


class CveDetailResponse(BaseModel):
    id: str
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    affected_ranges: str
    aliases: str
    source_url: str
    published_at: datetime
    fetched_at: datetime
    linked_servers: list[LinkedServer]

    class Config:
        from_attributes = True


router = APIRouter()


@router.get("/api/cve/{advisory_id}", response_model=CveDetailResponse)
def get_cve_detail(advisory_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    result = session.execute(
        text("""
            SELECT
                va.id, va.feed, va.summary, va.severity, va.ecosystem,
                va.package, va.affected_ranges, va.aliases, va.source_url,
                va.published_at, va.fetched_at,
                msr.server_id, msr.name AS server_name,
                vl.match_confidence, vl.linked_at
            FROM vuln_advisories va
            LEFT JOIN vuln_links vl ON va.id = vl.advisory_id
            LEFT JOIN mcp_server_registry msr ON vl.server_id = msr.server_id
            WHERE va.id = :advisory_id
        """),
        {"advisory_id": advisory_id}
    ).fetchall()

    if not result:
        return None

    row = result[0]
    linked_servers = []
    for r in result:
        if r.server_id and r.match_confidence is not None:
            linked_servers.append({
                "server_id": r.server_id,
                "server_name": r.server_name,
                "match_confidence": r.match_confidence,
                "linked_at": r.linked_at
            })

    return {
        "id": row.id,
        "feed": row.feed,
        "summary": row.summary,
        "severity": row.severity,
        "ecosystem": row.ecosystem,
        "package": row.package,
        "affected_ranges": row.affected_ranges,
        "aliases": row.aliases,
        "source_url": row.source_url,
        "published_at": row.published_at,
        "fetched_at": row.fetched_at,
        "linked_servers": linked_servers
    }


def main():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )

    with engine.connect() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                url VARCHAR,
                registry_source VARCHAR,
                trust_score FLOAT,
                confidence VARCHAR,
                description TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                meta VARCHAR,
                risk_tier VARCHAR,
                scan_count VARCHAR,
                verdict VARCHAR,
                verdict_reasoning TEXT
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE vuln_advisories (
                id VARCHAR PRIMARY KEY,
                feed VARCHAR,
                summary VARCHAR,
                severity VARCHAR,
                ecosystem VARCHAR,
                package VARCHAR,
                affected_ranges VARCHAR,
                aliases VARCHAR,
                source_url VARCHAR,
                published_at TIMESTAMP,
                fetched_at TIMESTAMP,
                content_hash VARCHAR,
                identities VARCHAR
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE vuln_links (
                advisory_id VARCHAR,
                id VARCHAR,
                linked_at TIMESTAMP,
                match_basis VARCHAR,
                match_confidence FLOAT,
                match_value VARCHAR,
                server_id VARCHAR,
                PRIMARY KEY (advisory_id, id, server_id)
            )
        """)
        conn.commit()

        conn.exec_driver_sql("""
            INSERT INTO mcp_server_registry (server_id, name, url, registry_source, trust_score, confidence)
            VALUES ('srv-001', 'Server Alpha', 'https://alpha.example.com', 'test', 0.8, 'high')
        """)
        conn.exec_driver_sql("""
            INSERT INTO mcp_server_registry (server_id, name, url, registry_source, trust_score, confidence)
            VALUES ('srv-002', 'Server Beta', 'https://beta.example.com', 'test', 0.9, 'medium')
        """)
        conn.exec_driver_sql("""
            INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, affected_ranges, aliases, source_url, published_at, fetched_at)
            VALUES ('ADV-001', 'NVD', 'Test advisory 1', 'HIGH', 'PyPI', 'test-package', '>=1.0.0,<2.0.0', 'CVE-2024-0001', 'https://example.com/adv1', '2024-01-01 00:00:00', '2024-01-15 00:00:00')
        """)
        conn.exec_driver_sql("""
            INSERT INTO vuln_advisories (id, feed, summary, severity, ecosystem, package, affected_ranges, aliases, source_url, published_at, fetched_at)
            VALUES ('ADV-002', 'NVD', 'Test advisory 2', 'CRITICAL', 'npm', 'test-package-2', '>=0.5.0', 'CVE-2024-0002', 'https://example.com/adv2', '2024-02-01 00:00:00', '2024-02-15 00:00:00')
        """)
        conn.exec_driver_sql("""
            INSERT INTO vuln_links (advisory_id, id, server_id, match_confidence, linked_at, match_basis, match_value)
            VALUES ('ADV-001', 'link-001', 'srv-001', 0.95, '2024-01-20 00:00:00', 'package', 'test-package')
        """)
        conn.exec_driver_sql("""
            INSERT INTO vuln_links (advisory_id, id, server_id, match_confidence, linked_at, match_basis, match_value)
            VALUES ('ADV-001', 'link-002', 'srv-002', 0.85, '2024-01-21 00:00:00', 'package', 'test-package')
        """)
        conn.exec_driver_sql("""
            INSERT INTO vuln_links (advisory_id, id, server_id, match_confidence, linked_at, match_basis, match_value)
            VALUES ('ADV-002', 'link-003', 'srv-001', 0.75, '2024-02-25 00:00:00', 'package', 'test-package-2')
        """)
        conn.commit()

    from sqlalchemy.orm import sessionmaker
    TestingSessionLocal = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    client = TestClient(app)
    response = client.get("/api/cve/ADV-001")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["id"] == "ADV-001"
    assert data["feed"] == "NVD"
    assert data["summary"] == "Test advisory 1"
    assert data["severity"] == "HIGH"
    assert data["ecosystem"] == "PyPI"
    assert data["package"] == "test-package"
    assert data["affected_ranges"] == ">=1.0.0,<2.0.0"
    assert data["aliases"] == "CVE-2024-0001"
    assert data["source_url"] == "https://example.com/adv1"
    assert len(data["linked_servers"]) >= 1, f"Expected at least 1 linked server, got {len(data['linked_servers'])}"

    print("PASS")


if __name__ == "__main__":
    main()