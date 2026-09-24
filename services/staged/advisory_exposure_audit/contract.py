from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, case, join
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["audit"])


class ServerRisk(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    severity: str
    cve_count: int


class AuditAdvisoryExposureResponse(BaseModel):
    coverage_pct: float
    total_servers: int
    servers_with_advisories: int
    avg_cves_per_affected_server: float
    critical_count: int
    high_count: int
    severity_breakdown: dict
    top_10_riskiest: list


def get_advisory_exposure(db: Session) -> AuditAdvisoryExposureResponse:
    server_severity_subq = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            func.max(
                case(
                    (VulnAdvisory.severity == "CRITICAL", "CRITICAL"),
                    (VulnAdvisory.severity == "HIGH", "HIGH"),
                    (VulnAdvisory.severity == "MEDIUM", "MEDIUM"),
                    (VulnAdvisory.severity == "LOW", "LOW"),
                    else_="NONE",
                )
            ).label("severity"),
        )
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .outerjoin(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .group_by(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.risk_tier)
        .subquery()
    )

    top_10_query = (
        select(
            server_severity_subq.c.server_id,
            server_severity_subq.c.name,
            server_severity_subq.c.risk_tier,
            server_severity_subq.c.severity,
        )
        .select_from(server_severity_subq)
        .outerjoin(VulnLink, VulnLink.server_id == server_severity_subq.c.server_id)
        .group_by(
            server_severity_subq.c.server_id,
            server_severity_subq.c.name,
            server_severity_subq.c.risk_tier,
            server_severity_subq.c.severity,
        )
        .order_by(
            case(
                (server_severity_subq.c.severity == "CRITICAL", 1),
                (server_severity_subq.c.severity == "HIGH", 2),
                (server_severity_subq.c.severity == "MEDIUM", 3),
                (server_severity_subq.c.severity == "LOW", 4),
                else_=5,
            ),
            func.count(VulnLink.id).desc().nullslast(),
        )
        .limit(10)
    )

    top_10_rows = db.execute(top_10_query).all()

    cve_count_subq = (
        select(
            VulnLink.server_id,
            func.count(VulnLink.id).label("cve_count"),
        )
        .group_by(VulnLink.server_id)
        .subquery()
    )

    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar()

    servers_with_advisories = db.query(func.count(func.distinct(VulnLink.server_id))).scalar()

    avg_cves = db.query(func.avg(cve_count_subq.c.cve_count)).scalar() or 0.0

    severity_counts = db.query(
        func.count(
            case((server_severity_subq.c.severity == "CRITICAL", 1))
        ),
        func.count(
            case((server_severity_subq.c.severity == "HIGH", 1))
        ),
        func.count(
            case((server_severity_subq.c.severity == "MEDIUM", 1))
        ),
        func.count(
            case((server_severity_subq.c.severity == "LOW", 1))
        ),
        func.count(
            case((server_severity_subq.c.severity == "NONE", 1))
        ),
    ).select_from(server_severity_subq).one()

    coverage_pct = (servers_with_advisories / total_servers * 100) if total_servers else 0.0

    top_10_riskiest = []
    for row in top_10_rows:
        cve_q = (
            db.query(func.count(VulnLink.id))
            .filter(VulnLink.server_id == row.server_id)
            .scalar()
        )
        top_10_riskiest.append(
            ServerRisk(
                server_id=row.server_id,
                name=row.name,
                risk_tier=row.risk_tier,
                severity=row.severity,
                cve_count=cve_q or 0,
            )
        )

    return AuditAdvisoryExposureResponse(
        coverage_pct=round(coverage_pct, 1),
        total_servers=total_servers,
        servers_with_advisories=servers_with_advisories,
        avg_cves_per_affected_server=round(avg_cves, 2),
        critical_count=severity_counts[0] or 0,
        high_count=severity_counts[1] or 0,
        severity_breakdown={
            "CRITICAL": severity_counts[0] or 0,
            "HIGH": severity_counts[1] or 0,
            "MEDIUM": severity_counts[2] or 0,
            "LOW": severity_counts[3] or 0,
            "NONE": severity_counts[4] or 0,
        },
        top_10_riskiest=top_10_riskiest,
    )


@router.get("/audit/advisory-exposure", response_model=AuditAdvisoryExposureResponse)
def get_advisory_exposure_endpoint(db: Session = Depends(get_session)):
    return get_advisory_exposure(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE mcp_server_registry (server_id TEXT PRIMARY KEY, name TEXT, url TEXT, risk_tier TEXT, first_seen TEXT, last_seen TEXT, last_scanned TEXT, last_assessed TEXT, description TEXT, registry_source TEXT, scan_count INTEGER, trust_score REAL, confidence REAL, verdict TEXT, verdict_reasoning TEXT, meta TEXT)"))

        conn.execute(text("""CREATE TABLE vuln_advisories (
            id TEXT PRIMARY KEY,
            package TEXT,
            ecosystem TEXT,
            summary TEXT,
            severity TEXT,
            published_at TEXT,
            fetched_at TEXT,
            source_url TEXT,
            aliases TEXT,
            content_hash TEXT,
            affected_ranges TEXT,
            identities TEXT,
            feed TEXT
        )"""))

        conn.execute(text("""CREATE TABLE vuln_links (
            id TEXT PRIMARY KEY,
            advisory_id TEXT,
            server_id TEXT,
            match_value TEXT,
            match_basis TEXT,
            match_confidence REAL,
            linked_at TEXT
        )"""))

    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        from datetime import datetime
        now = datetime.utcnow().isoformat()

        conn.execute(text("INSERT INTO mcp_server_registry (server_id, name, url, risk_tier, first_seen, last_seen) VALUES (:s, :n, :u, :r, :f, :l)"), [
            {"s": "srv1", "n": "Server One", "u": "http://srv1.local", "r": "critical", "f": now, "l": now},
            {"s": "srv2", "n": "Server Two", "u": "http://srv2.local", "r": "high", "f": now, "l": now},
            {"s": "srv3", "n": "Server Three", "u": "http://srv3.local", "r": "medium", "f": now, "l": now},
            {"s": "srv4", "n": "Server Four", "u": "http://srv4.local", "r": "low", "f": now, "l": now},
            {"s": "srv5", "n": "Server Five", "u": "http://srv5.local", "r": "low", "f": now, "l": now},
        ])

        conn.execute(text("INSERT INTO vuln_advisories (id, package, severity, ecosystem, summary, published_at, fetched_at, source_url) VALUES (:id, :p, :s, :e, :sum, :pub, :fet, :src)"), [
            {"id": "adv1", "p": "pkg1", "s": "CRITICAL", "e": "npm", "sum": "Critical vuln", "pub": now, "fet": now, "src": "http://example.com/adv1"},
            {"id": "adv2", "p": "pkg2", "s": "HIGH", "e": "pip", "sum": "High vuln", "pub": now, "fet": now, "src": "http://example.com/adv2"},
        ])

        conn.execute(text("INSERT INTO vuln_links (id, advisory_id, server_id, match_value, linked_at) VALUES (:id, :aid, :sid, :mv, :la)"), [
            {"id": "lnk1", "aid": "adv1", "sid": "srv1", "mv": "match1", "la": now},
            {"id": "lnk2", "aid": "adv1", "sid": "srv3", "mv": "match2", "la": now},
            {"id": "lnk3", "aid": "adv2", "sid": "srv2", "mv": "match3", "la": now},
        ])

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/audit/advisory-exposure")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["coverage_pct"] == 60.0, f"Expected coverage_pct=60.0, got {data['coverage_pct']}"
    assert "CRITICAL" in data["severity_breakdown"]
    assert "HIGH" in data["severity_breakdown"]
    assert "MEDIUM" in data["severity_breakdown"]
    assert "LOW" in data["severity_breakdown"]
    assert "NONE" in data["severity_breakdown"]
    assert len(data["top_10_riskiest"]) <= 10

    print("PASS")