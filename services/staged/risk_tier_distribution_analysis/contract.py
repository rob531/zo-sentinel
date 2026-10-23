"""
Contract self-test for risk_tier_distribution_analysis service.
"""
from __future__ import annotations

from typing import Any, Generator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session


class TierStats(BaseModel):
    count: int
    percentage: float
    avg_score: float


class RiskDistributionResponse(BaseModel):
    tier: dict[str, TierStats]


def get_distribution_stats(session: Session) -> dict[str, Any]:
    """Compute distribution statistics for each risk tier."""
    query = text("""
        WITH tier_stats AS (
            SELECT 
                s.risk_tier,
                COUNT(DISTINCT s.server_id) as cnt,
                AVG(sc.p_critical) as avg_score
            FROM McpServerRegistry s
            LEFT JOIN McpLlmAxisScore sc ON s.server_id = sc.server_id
            GROUP BY s.risk_tier
        ),
        total AS (
            SELECT SUM(cnt) as total_cnt FROM tier_stats
        )
        SELECT 
            ts.risk_tier,
            ts.cnt as count,
            CASE 
                WHEN t.total_cnt > 0 THEN ROUND(ts.cnt * 100.0 / t.total_cnt, 2)
                ELSE 0 
            END as percentage,
            COALESCE(ts.avg_score, 0) as avg_score
        FROM tier_stats ts
        CROSS JOIN total t
    """)
    result = session.execute(query)
    rows = result.fetchall()
    
    distribution = {}
    for row in rows:
        risk_tier = row[0] if row[0] else "unknown"
        distribution[risk_tier] = {
            "count": row[1],
            "percentage": float(row[2]),
            "avg_score": float(row[3]) if row[3] else 0.0
        }
    
    return {"tier": distribution}


def create_router() -> Any:
    from fastapi import APIRouter
    router = APIRouter()
    
    @router.get("/api/risk/distribution/analysis", response_model=RiskDistributionResponse)
    def get_risk_distribution_analysis(
        session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        return get_distribution_stats(session)
    
    return router


def create_app() -> FastAPI:
    app = FastAPI(title="Risk Tier Distribution Analysis")
    router = create_router()
    app.include_router(router)
    return app


def main() -> None:
    """Self-test: seeds 3 servers with different risk tiers, asserts 200 and stats."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                risk_tier TEXT,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                confidence REAL,
                trust_score REAL,
                description TEXT,
                meta TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                id TEXT PRIMARY KEY,
                server_id TEXT,
                adapter_sha256 TEXT,
                model_version TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                probs TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                decision_rule_version TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                scored_at TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO McpServerRegistry (server_id, risk_tier, name, url, registry_source)
            VALUES 
                ('srv1', 'critical', 'Server One', 'http://example1.com', 'test'),
                ('srv2', 'high', 'Server Two', 'http://example2.com', 'test'),
                ('srv3', 'medium', 'Server Three', 'http://example3.com', 'test')
        """))
        conn.execute(text("""
            INSERT INTO McpLlmAxisScore (id, server_id, p_critical, scored_at)
            VALUES 
                ('sc1', 'srv1', 0.9, '2024-01-01'),
                ('sc2', 'srv2', 0.6, '2024-01-01'),
                ('sc3', 'srv3', 0.3, '2024-01-01')
        """))
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    response = client.get("/api/risk/distribution/analysis")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    
    assert "tier" in data, f"Response missing 'tier' key: {data}"
    distribution = data["tier"]
    
    assert "high" in distribution, f"Expected 'high' tier in distribution, got {list(distribution.keys())}"
    assert distribution["high"]["count"] == 1, f"Expected count=1 for high tier, got {distribution['high']['count']}"
    assert abs(distribution["high"]["percentage"] - 33.33) < 0.1, \
        f"Expected percentage ~33.33 for high tier, got {distribution['high']['percentage']}"
    
    print("PASS")


if __name__ == "__main__":
    main()