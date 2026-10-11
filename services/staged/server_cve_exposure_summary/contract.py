from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.db import get_session
from app.models import VulnAdvisory, VulnLink
import sys

router = APIRouter(prefix="/api")


class SeverityBreakdown(BaseModel):
    count: int
    examples: list[str]


class CVEExposureSummary(BaseModel):
    server_id: int
    total_unique_cves: int
    by_severity: dict[str, SeverityBreakdown]


@router.get("/servers/{server_id}/cve-exposure", response_model=CVEExposureSummary)
def get_cve_exposure(
    server_id: int,
    session: Session = Depends(get_session),
) -> CVEExposureSummary:
    result = session.execute(
        text("""
            SELECT
                va.severity,
                COUNT(DISTINCT va.identities) as cve_count,
                GROUP_CONCAT(DISTINCT va.identities) as cve_examples
            FROM vuln_links vl
            JOIN vuln_advisories va ON vl.advisory_id = va.id
            WHERE vl.server_id = :server_id
            GROUP BY va.severity
        """),
        {"server_id": server_id},
    )
    rows = result.fetchall()

    severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
    by_severity = {}
    total_unique_cves = 0

    severity_data = {row.severity: {"count": row.cve_count, "examples": (row.cve_examples or "").split(",") if row.cve_examples else []} for row in rows}

    for sev in severities:
        data = severity_data.get(sev, {"count": 0, "examples": []})
        by_severity[sev] = SeverityBreakdown(
            count=data["count"],
            examples=data["examples"][:5] if data["examples"] else [],
        )
        total_unique_cves += data["count"]

    return CVEExposureSummary(
        server_id=server_id,
        total_unique_cves=total_unique_cves,
        by_severity=by_severity,
    )


def create_app():
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return app


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE vuln_advisories (
                id INTEGER PRIMARY KEY,
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
                advisory_id INTEGER,
                linked_at TEXT,
                match_basis TEXT,
                match_confidence REAL,
                match_value TEXT,
                server_id INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                id INTEGER PRIMARY KEY,
                name TEXT,
                org_id INTEGER
            )
        """))

        conn.execute(text("""
            INSERT INTO mcp_server_registry (id, name, org_id) VALUES
            (1, 'server1', 1),
            (2, 'server2', 1),
            (3, 'server3', 1)
        """))

        conn.execute(text("""
            INSERT INTO vuln_advisories (id, severity, identities, package, summary, aliases, affected_ranges, content_hash, ecosystem, feed, fetched_at, published_at, source_url)
            VALUES
            (1, 'CRITICAL', 'CVE-2021-44228', 'log4j', 'Log4Shell', 'CVE-2021-44228', '*', 'abc123', 'maven', 'nvd', '2021-12-10', '2021-12-10', 'https://nvd.nist.gov'),
            (2, 'HIGH', 'CVE-2022-12345', 'openssl', 'OpenSSL vuln', 'CVE-2022-12345', '[1.0.0,]', 'def456', 'npm', 'ghsa', '2022-01-15', '2022-01-15', 'https://github.com'),
            (3, 'MEDIUM', 'CVE-2023-99999', 'curl', 'cURL issue', 'CVE-2023-99999', '[7.0.0,8.0.0)', 'ghi789', 'pip', 'snyk', '2023-06-20', '2023-06-20', 'https://snyk.io')
        """))

        conn.execute(text("""
            INSERT INTO vuln_links (id, advisory_id, server_id, linked_at, match_basis, match_confidence, match_value)
            VALUES
            (1, 1, 1, '2021-12-15', 'package', 0.95, 'log4j'),
            (2, 2, 1, '2022-01-20', 'package', 0.90, 'openssl'),
            (3, 3, 2, '2023-06-25', 'package', 0.85, 'curl'),
            (4, 1, 3, '2021-12-16', 'package', 0.95, 'log4j')
        """))

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/servers/1/cve-exposure")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["total_unique_cves"] > 0, f"Expected total_unique_cves > 0, got {data['total_unique_cves']}"
    assert "CRITICAL" in data["by_severity"], f"Expected CRITICAL key in by_severity: {data['by_severity'].keys()}"
    assert "HIGH" in data["by_severity"], f"Expected HIGH key in by_severity: {data['by_severity'].keys()}"

    print("PASS")
    sys.exit(0)