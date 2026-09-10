from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from app.db import get_session
from app.models import McpServerRegistry, VulnLink, VulnAdvisory

router = APIRouter(prefix="/api", tags=["risk-analytics"])


class TopSeverityItem(BaseModel):
    severity: str
    count: int


class TopCveItem(BaseModel):
    id: str
    severity: str
    count: int


class RiskAnalyticsResponse(BaseModel):
    server_count: int
    tier_distribution: dict
    verdict_distribution: dict
    avg_confidence: float
    avg_scan_count: float
    top_vuln_severities: list[TopSeverityItem]
    top_cve_ids: list[TopCveItem]


@router.get("/servers/risk-analytics", response_model=RiskAnalyticsResponse)
def get_risk_analytics(session=Depends(get_session)):
    server_count_query = session.execute(
        text("SELECT COUNT(*) FROM mcp_server_registry")
    ).scalar()
    server_count = server_count_query or 0

    tier_query = session.execute(
        text("""
            SELECT risk_tier, COUNT(*) 
            FROM mcp_server_registry 
            GROUP BY risk_tier
        """)
    ).fetchall()
    tier_distribution = {row[0]: row[1] for row in tier_query}

    verdict_query = session.execute(
        text("""
            SELECT verdict, COUNT(*) 
            FROM mcp_server_registry 
            GROUP BY verdict
        """)
    ).fetchall()
    verdict_distribution = {row[0]: row[1] for row in verdict_query}

    avg_query = session.execute(
        text("""
            SELECT 
                COALESCE(AVG(confidence), 0),
                COALESCE(AVG(scan_count), 0)
            FROM mcp_server_registry
        """)
    ).fetchone()
    avg_confidence = float(avg_query[0]) if avg_query else 0.0
    avg_scan_count = float(avg_query[1]) if avg_query else 0.0

    severity_query = session.execute(
        text("""
            SELECT va.severity, COUNT(*) as cnt
            FROM vuln_links vl
            JOIN vuln_advisories va ON vl.advisory_id = va.id
            GROUP BY va.severity
            ORDER BY cnt DESC
            LIMIT 5
        """)
    ).fetchall()
    top_vuln_severities = [
        {"severity": row[0], "count": row[1]} for row in severity_query
    ]

    cve_query = session.execute(
        text("""
            SELECT va.id, va.severity, COUNT(*) as cnt
            FROM vuln_links vl
            JOIN vuln_advisories va ON vl.advisory_id = va.id
            GROUP BY va.id, va.severity
            ORDER BY cnt DESC
            LIMIT 10
        """)
    ).fetchall()
    top_cve_ids = [
        {"id": row[0], "severity": row[1], "count": row[2]} for row in cve_query
    ]

    return RiskAnalyticsResponse(
        server_count=server_count,
        tier_distribution=tier_distribution,
        verdict_distribution=verdict_distribution,
        avg_confidence=avg_confidence,
        avg_scan_count=avg_scan_count,
        top_vuln_severities=top_vuln_severities,
        top_cve_ids=top_cve_ids,
    )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "services/staged")
    from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Float, DateTime
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()

    Table(
        "mcp_server_registry", metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", String),
        Column("name", String),
        Column("url", String),
        Column("risk_tier", String),
        Column("verdict", String),
        Column("confidence", Float),
        Column("scan_count", Integer),
        Column("last_assessed", DateTime),
        Column("registry_source", String),
    )

    Table(
        "vuln_links", metadata,
        Column("id", Integer, primary_key=True),
        Column("server_id", String),
        Column("advisory_id", String),
        Column("match_value", String),
        Column("match_confidence", Float),
    )

    Table(
        "vuln_advisories", metadata,
        Column("id", String, primary_key=True),
        Column("severity", String),
        Column("summary", String),
        Column("ecosystem", String),
        Column("published_at", DateTime),
    )

    metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    now = datetime.now()
    servers = [
        ("srv-001", "trusted-server-1", "TRUSTED_GENERAL", "TRUSTED_GENERAL", 0.85, 10),
        ("srv-002", "trusted-server-2", "TRUSTED_GENERAL", "TRUSTED_GENERAL", 0.90, 5),
        ("srv-003", "risky-server-1", "MEDIUM_RISK", "MONITOR", 0.45, 20),
        ("srv-004", "risky-server-2", "HIGH_RISK", "REVIEW", 0.30, 15),
        ("srv-005", "unknown-server", "UNKNOWN", "UNKNOWN", 0.50, 2),
    ]
    for sid, name, tier, verd, conf, scans in servers:
        session.execute(
            text("""
                INSERT INTO mcp_server_registry 
                (server_id, name, url, risk_tier, verdict, confidence, scan_count, last_assessed, registry_source)
                VALUES (:sid, :name, :url, :tier, :verd, :conf, :scans, :dt, 'test')
            """),
            {"sid": sid, "name": name, "url": f"http://{name}.test", "tier": tier,
             "verd": verd, "conf": conf, "scans": scans, "dt": now}
        )

    advisories = [
        ("CVE-2024-0001", "CRITICAL", "Critical RCE vulnerability"),
        ("CVE-2024-0002", "HIGH", "High severity privilege escalation"),
        ("CVE-2024-0003", "MEDIUM", "Medium severity DoS"),
    ]
    for adv_id, sev, summ in advisories:
        session.execute(
            text("""
                INSERT INTO vuln_advisories (id, severity, summary, ecosystem, published_at)
                VALUES (:id, :sev, :summ, 'pypi', :dt)
            """),
            {"id": adv_id, "sev": sev, "summ": summ, "dt": now}
        )

    vuln_links = [
        ("srv-001", "CVE-2024-0001"),
        ("srv-001", "CVE-2024-0002"),
        ("srv-002", "CVE-2024-0003"),
    ]
    for idx, (srv, adv) in enumerate(vuln_links, 1):
        session.execute(
            text("""
                INSERT INTO vuln_links (server_id, advisory_id, match_value, match_confidence)
                VALUES (:srv, :adv, :val, :conf)
            """),
            {"srv": srv, "adv": adv, "val": f"match-{idx}", "conf": 0.95}
        )

    session.commit()

    def override_get_session():
        return session

    app = FastAPI()
    app.include_router(router)

    from fastapi.testclient import TestClient
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get("/api/servers/risk-analytics")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert "tier_distribution" in data, "Missing tier_distribution"
    assert any("TRUSTED_GENERAL" in k for k in data["tier_distribution"].keys()), \
        "Missing TRUSTED_GENERAL in tier_distribution"

    assert "top_vuln_severities" in data, "Missing top_vuln_severities"
    assert len(data["top_vuln_severities"]) > 0, "top_vuln_severities is empty"

    print("PASS")