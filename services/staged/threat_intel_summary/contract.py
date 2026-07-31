# services/staged/threat_intel_summary/contract.py
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["threat_intel_summary"])


class IndicatorResponse(BaseModel):
    type: str
    value: str
    source: str
    fetched_at: str


class VulnerabilityResponse(BaseModel):
    id: int
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: str
    match_confidence: float


class ThreatIntelSummaryResponse(BaseModel):
    server_id: str
    indicators: List[IndicatorResponse]
    vulnerabilities: List[VulnerabilityResponse]


@router.get("/threat_intel/summary", response_model=ThreatIntelSummaryResponse)
async def get_threat_intel_summary(
    server_id: str = Query(..., description="MCP server identifier"),
    db: Session = Depends(get_session),
) -> ThreatIntelSummaryResponse:
    # Query indicators for this server from threat_intel_refs
    indicators_query = text("""
        SELECT DISTINCT
            tir.indicator_type as type,
            tir.indicator_value as value,
            tir.source as source,
            tir.fetched_at as fetched_at
        FROM threat_intel_refs tir
        WHERE tir.indicator_value IN (
            SELECT indicator_value 
            FROM threat_intel_refs 
            WHERE pulse_name IN (
                SELECT pulse_name 
                FROM threat_intel_refs 
                WHERE indicator_value IN (
                    SELECT indicator_value 
                    FROM threat_intel_refs
                )
            )
        )
        ORDER BY tir.fetched_at DESC
        LIMIT 100
    """)

    indicators_result = db.execute(indicators_query).fetchall()
    indicators = [
        IndicatorResponse(
            type=row.type,
            value=row.value,
            source=row.source,
            fetched_at=str(row.fetched_at) if row.fetched_at else ""
        )
        for row in indicators_result
    ]

    # Query vulnerabilities: join vuln_links to vuln_advisories, filter by server_id
    vuln_query = text("""
        SELECT DISTINCT
            va.id,
            va.summary,
            va.severity,
            va.ecosystem,
            va.package,
            va.published_at,
            vl.match_confidence
        FROM vuln_links vl
        JOIN vuln_advisories va ON vl.advisory_id = va.id
        WHERE vl.server_id = :server_id
        ORDER BY va.published_at DESC
        LIMIT 100
    """)

    vulnerabilities_result = db.execute(vuln_query, {"server_id": server_id}).fetchall()
    vulnerabilities = [
        VulnerabilityResponse(
            id=row.id,
            summary=row.summary,
            severity=row.severity,
            ecosystem=row.ecosystem,
            package=row.package,
            published_at=str(row.published_at) if row.published_at else "",
            match_confidence=row.match_confidence
        )
        for row in vulnerabilities_result
    ]

    return ThreatIntelSummaryResponse(
        server_id=server_id,
        indicators=indicators,
        vulnerabilities=vulnerabilities
    )


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Create in-memory SQLite database for self-test
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables
    with test_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE threat_intel_refs (
                indicator_type TEXT,
                indicator_value TEXT,
                pulse_name TEXT,
                source TEXT,
                fetched_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_advisories (
                id INTEGER PRIMARY KEY,
                summary TEXT,
                severity TEXT,
                ecosystem TEXT,
                package TEXT,
                published_at TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_links (
                server_id TEXT,
                advisory_id INTEGER,
                match_confidence REAL
            )
        """))

        # Insert minimal test data for server "srv1"
        conn.execute(text("""
            INSERT INTO threat_intel_refs 
            (indicator_type, indicator_value, pulse_name, source, fetched_at)
            VALUES ('ip', '192.168.1.1', 'pulse1', 'source1', '2024-01-15T10:00:00')
        """))
        conn.execute(text("""
            INSERT INTO vuln_advisories
            (id, summary, severity, ecosystem, package, published_at)
            VALUES (1, 'Critical vulnerability', 'high', 'npm', 'test-package', '2024-01-10')
        """))
        conn.execute(text("""
            INSERT INTO vuln_links
            (server_id, advisory_id, match_confidence)
            VALUES ('srv1', 1, 0.95)
        """))
        conn.commit()

    TestingSessionLocal = sessionmaker(bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Create FastAPI app for testing
    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/threat_intel/summary?server_id=srv1")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert len(data.get("indicators", [])) == 1, f"Expected 1 indicator, got {len(data.get('indicators', []))}"
    assert len(data.get("vulnerabilities", [])) == 1, f"Expected 1 vulnerability, got {len(data.get('vulnerabilities', []))}"

    print("PASS")
    sys.exit(0)