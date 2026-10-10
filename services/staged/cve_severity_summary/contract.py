from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/cve/severity-summary")
    def get_cve_severity_summary(db: Session = Depends(get_session)) -> dict[str, Any]:
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        sixty_days_ago = thirty_days_ago - timedelta(days=30)

        severity_counts = db.execute(
            select(
                func.lower(VulnAdvisory.severity).label("severity"),
                func.count(VulnAdvisory.id).label("count"),
            )
            .where(VulnAdvisory.severity.isnot(None))
            .group_by(func.lower(VulnAdvisory.severity))
        ).all()

        summary = {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}
        for row in severity_counts:
            sev = row.severity
            if sev in summary:
                summary[sev] = row.count
                summary["total"] += row.count

        recent_cves = db.execute(
            select(func.count(VulnAdvisory.id))
            .where(VulnAdvisory.published_at >= thirty_days_ago)
        ).scalar() or 0

        prior_cves = db.execute(
            select(func.count(VulnAdvisory.id))
            .where(VulnAdvisory.published_at >= sixty_days_ago)
            .where(VulnAdvisory.published_at < thirty_days_ago)
        ).scalar() or 0

        if prior_cves > 0:
            trend_pct = round(((recent_cves - prior_cves) / prior_cves) * 100, 2)
        elif recent_cves > 0:
            trend_pct = 100.0
        else:
            trend_pct = 0.0

        return {
            "summary": summary,
            "velocity": {
                "recent": recent_cves,
                "prior": prior_cves,
                "trend_pct": trend_pct,
            },
            "as_of": datetime.utcnow().isoformat(),
        }

    return app


def run_self_test() -> None:
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
                severity TEXT,
                ecosystem TEXT,
                published_at TIMESTAMP,
                package TEXT,
                summary TEXT,
                content_hash TEXT,
                feed TEXT,
                source_url TEXT,
                fetched_at TIMESTAMP,
                affected_ranges TEXT,
                aliases TEXT,
                identities TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY,
                advisory_id INTEGER,
                server_id TEXT,
                match_value TEXT,
                match_basis TEXT,
                match_confidence REAL,
                linked_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT
            )
        """))

    now = datetime.utcnow()
    advisories = [
        (1, "CRITICAL", "npm", now - timedelta(days=5)),
        (2, "CRITICAL", "pypi", now - timedelta(days=10)),
        (3, "HIGH", "npm", now - timedelta(days=15)),
        (4, "HIGH", "pypi", now - timedelta(days=25)),
        (5, "HIGH", "npm", now - timedelta(days=35)),
        (6, "MEDIUM", "npm", now - timedelta(days=40)),
        (7, "MEDIUM", "cargo", now - timedelta(days=50)),
        (8, "LOW", "npm", now - timedelta(days=55)),
    ]

    with engine.begin() as conn:
        for adv_id, severity, ecosystem, published_at in advisories:
            conn.execute(text("""
                INSERT INTO vuln_advisories (id, severity, ecosystem, published_at)
                VALUES (:id, :severity, :ecosystem, :published_at)
            """), {"id": adv_id, "severity": severity, "ecosystem": ecosystem, "published_at": published_at})

    app = create_app()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/cve/severity-summary")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    summary = data.get("summary", {})
    velocity = data.get("velocity", {})

    assert "critical" in summary, "Missing critical in summary"
    assert "high" in summary, "Missing high in summary"
    assert "medium" in summary, "Missing medium in summary"
    assert "low" in summary, "Missing low in summary"

    assert isinstance(velocity.get("trend_pct"), (int, float)), "trend_pct must be numeric"

    print("PASS")


if __name__ == "__main__":
    run_self_test()