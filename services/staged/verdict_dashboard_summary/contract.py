from datetime import datetime, timedelta
from typing import Optional
import math

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


# Pydantic models for response
class TierSummary(BaseModel):
    tier: str
    count: int
    pct: float


class GrowthMetrics(BaseModel):
    d7: int
    d30: int
    d90: int


class SourceSummary(BaseModel):
    source: str
    count: int


class TopServer(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    trust_score: float


class Totals(BaseModel):
    servers: int
    avg_trust: float


class VerdictSummaryResponse(BaseModel):
    totals: Totals
    tiers: list[TierSummary]
    growth: GrowthMetrics
    sources: list[SourceSummary]
    top_servers: list[TopServer]


RISK_TIERS = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
]


def create_app() -> FastAPI:
    app = FastAPI(title="verdict_dashboard_summary")

    @app.get("/api/dashboard/verdict-summary", response_model=VerdictSummaryResponse)
    def get_verdict_summary(db: Session = Depends(get_session)) -> VerdictSummaryResponse:
        now = datetime.utcnow()
        cutoff_7 = now - timedelta(days=7)
        cutoff_30 = now - timedelta(days=30)
        cutoff_90 = now - timedelta(days=90)

        # Total servers and avg trust
        total_result = db.execute(text("""
            SELECT COUNT(*) as total, COALESCE(AVG(trust_score), 0) as avg_trust
            FROM mcp_server_registry
        """)).fetchone()
        total_servers = total_result[0] if total_result else 0
        avg_trust = float(total_result[1]) if total_result and total_result[1] else 0.0

        # Count per risk tier
        tier_results = db.execute(text("""
            SELECT risk_tier, COUNT(*) as cnt
            FROM mcp_server_registry
            GROUP BY risk_tier
        """)).fetchall()

        tier_map = {row[0]: row[1] for row in tier_results}
        tiers = []
        for tier in RISK_TIERS:
            count = tier_map.get(tier, 0)
            pct = (count / total_servers * 100) if total_servers > 0 else 0.0
            tiers.append(TierSummary(tier=tier, count=count, pct=round(pct, 2)))

        # Growth metrics
        growth_result = db.execute(text("""
            SELECT 
                SUM(CASE WHEN first_seen >= :cutoff_7 THEN 1 ELSE 0 END) as d7,
                SUM(CASE WHEN first_seen >= :cutoff_30 THEN 1 ELSE 0 END) as d30,
                SUM(CASE WHEN first_seen >= :cutoff_90 THEN 1 ELSE 0 END) as d90
            FROM mcp_server_registry
        """), {"cutoff_7": cutoff_7, "cutoff_30": cutoff_30, "cutoff_90": cutoff_90}).fetchone()

        d7 = int(growth_result[0]) if growth_result and growth_result[0] else 0
        d30 = int(growth_result[1]) if growth_result and growth_result[1] else 0
        d90 = int(growth_result[2]) if growth_result and growth_result[2] else 0

        # Servers by registry_source
        source_results = db.execute(text("""
            SELECT registry_source, COUNT(*) as cnt
            FROM mcp_server_registry
            GROUP BY registry_source
            ORDER BY cnt DESC
        """)).fetchall()

        sources = [SourceSummary(source=row[0], count=row[1]) for row in source_results]

        # Top 5 by trust_score
        top_results = db.execute(text("""
            SELECT server_id, name, risk_tier, trust_score
            FROM mcp_server_registry
            ORDER BY trust_score DESC NULLS LAST
            LIMIT 5
        """)).fetchall()

        top_servers = [
            TopServer(
                server_id=row[0],
                name=row[1] or "",
                risk_tier=row[2] or "",
                trust_score=float(row[3]) if row[3] is not None else 0.0
            )
            for row in top_results
        ]

        return VerdictSummaryResponse(
            totals=Totals(servers=total_servers, avg_trust=round(avg_trust, 2)),
            tiers=tiers,
            growth=GrowthMetrics(d7=d7, d30=d30, d90=d90),
            sources=sources,
            top_servers=top_servers,
        )

    return app


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create table
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                confidence FLOAT,
                description TEXT,
                first_seen TIMESTAMP,
                last_assessed TIMESTAMP,
                last_scanned TIMESTAMP,
                last_seen TIMESTAMP,
                meta TEXT,
                name TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                scan_count INTEGER,
                server_id TEXT PRIMARY KEY,
                trust_score FLOAT,
                url TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.commit()

    session = SessionLocal()
    now = datetime.utcnow()

    # Seed 20 servers across 5 tiers
    tiers_for_seed = [
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED",
        "HIGH_RISK_ISOLATED",
    ]

    servers_data = []
    for i in range(20):
        tier = tiers_for_seed[i % 5]
        first_seen = now - timedelta(days=i * 5)
        servers_data.append({
            "server_id": f"srv_{i:03d}",
            "name": f"Server {i}",
            "risk_tier": tier,
            "trust_score": 50.0 + (i * 2.5),
            "registry_source": ["core", "community", "enterprise"][i % 3],
            "first_seen": first_seen,
            "confidence": 0.8,
            "description": f"Description {i}",
            "url": f"https://server{i}.example.com",
            "verdict": "approved",
            "verdict_reasoning": f"Reasoning {i}",
            "last_assessed": now - timedelta(days=i),
            "last_scanned": now - timedelta(days=i // 2),
            "last_seen": now - timedelta(days=i // 3),
            "scan_count": i + 1,
            "meta": "{}",
        })

    for srv in servers_data:
        session.execute(
            text("""
                INSERT INTO mcp_server_registry 
                (server_id, name, risk_tier, trust_score, registry_source, first_seen,
                 confidence, description, url, verdict, verdict_reasoning,
                 last_assessed, last_scanned, last_seen, scan_count, meta)
                VALUES 
                (:server_id, :name, :risk_tier, :trust_score, :registry_source, :first_seen,
                 :confidence, :description, :url, :verdict, :verdict_reasoning,
                 :last_assessed, :last_scanned, :last_seen, :scan_count, :meta)
            """),
            srv
        )
    session.commit()
    session.close()

    app = create_app()

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/dashboard/verdict-summary")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()

    assert data["totals"]["servers"] == 20, f"Expected 20 servers, got {data['totals']['servers']}"

    tier_counts = {t["tier"]: t["count"] for t in data["tiers"]}
    tier_sum = sum(tier_counts.values())
    assert tier_sum == 20, f"Tier counts sum to {tier_sum}, expected 20"

    assert data["growth"]["d7"] >= 0, "d7 growth should be non-negative"
    assert data["growth"]["d30"] >= 0, "d30 growth should be non-negative"
    assert data["growth"]["d90"] >= 0, "d90 growth should be non-negative"

    top_servers = data["top_servers"]
    if len(top_servers) >= 2:
        for i in range(len(top_servers) - 1):
            assert top_servers[i]["trust_score"] >= top_servers[i + 1]["trust_score"], \
                "Top servers should be sorted desc by trust_score"

    print("PASS")