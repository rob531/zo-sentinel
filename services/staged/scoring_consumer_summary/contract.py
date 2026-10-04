from fastapi import FastAPI, Depends
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from typing import Dict, Any
from datetime import datetime, timedelta

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

app = FastAPI()

@app.get("/api/scoring/summary")
def get_scoring_summary(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """GET /api/scoring/summary"""
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    # Get scored servers (servers with axis scores in last 30 days)
    scored_server_ids = [r[0] for r in db.query(McpLlmAxisScore.server_id).filter(
        McpLlmAxisScore.scored_at >= thirty_days_ago
    ).distinct().all()]
    scored_servers = len(scored_server_ids)
    
    # Tier breakdown for scored servers
    tier_breakdown = {}
    if scored_server_ids:
        tier_results = db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id)
        ).filter(
            McpServerRegistry.server_id.in_(scored_server_ids)
        ).group_by(McpServerRegistry.risk_tier).all()
        for tier, count in tier_results:
            if tier is not None:
                tier_breakdown[tier] = count
    
    # Axis stats aggregation
    axis_stats = []
    axis_results = db.query(
        McpLlmAxisScore.axis_name,
        func.count(McpLlmAxisScore.id),
        func.avg(McpLlmAxisScore.p_top),
        func.avg(McpLlmAxisScore.p_critical),
        func.avg(McpLlmAxisScore.p_danger),
        func.sum(McpLlmAxisScore.escalated)
    ).filter(
        McpLlmAxisScore.scored_at >= thirty_days_ago
    ).group_by(McpLlmAxisScore.axis_name).all()
    
    for row in axis_results:
        axis_stats.append({
            "axis_name": row[0],
            "count": row[1],
            "avg_p_top": row[2] or 0.0,
            "avg_p_critical": row[3] or 0.0,
            "avg_p_danger": row[4] or 0.0,
            "escalated_count": row[5] or 0
        })
    
    return {
        "scored_servers": scored_servers,
        "tier_breakdown": tier_breakdown,
        "axis_stats": axis_stats
    }


if __name__ == "__main__":
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                adapter_sha256 TEXT,
                axis_name TEXT NOT NULL,
                decision_rule_version TEXT,
                escalated INTEGER DEFAULT 0,
                escalated_to TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                probs TEXT,
                scored_at TIMESTAMP,
                server_id TEXT NOT NULL
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                confidence REAL,
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
                trust_score REAL,
                url TEXT,
                verdict TEXT,
                verdict_reasoning TEXT
            )
        """)
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    with TestingSessionLocal() as db:
        now = datetime.utcnow().isoformat()
        axis_scores = [
            {"server_id": "server1", "axis_name": "axis1", "p_top": 0.1, "p_critical": 0.2, "p_danger": 0.3, "escalated": 0, "scored_at": now},
            {"server_id": "server2", "axis_name": "axis1", "p_top": 0.15, "p_critical": 0.25, "p_danger": 0.35, "escalated": 0, "scored_at": now},
            {"server_id": "server1", "axis_name": "axis2", "p_top": 0.2, "p_critical": 0.3, "p_danger": 0.4, "escalated": 0, "scored_at": now},
            {"server_id": "server2", "axis_name": "axis2", "p_top": 0.25, "p_critical": 0.35, "p_danger": 0.45, "escalated": 1, "scored_at": now},
            {"server_id": "server1", "axis_name": "axis2", "p_top": 0.3, "p_critical": 0.4, "p_danger": 0.5, "escalated": 0, "scored_at": now},
        ]
        
        for record in axis_scores:
            db.execute(
                text("""INSERT INTO mcp_llm_axis_scores 
                    (server_id, axis_name, p_top, p_critical, p_danger, escalated, scored_at)
                    VALUES (:server_id, :axis_name, :p_top, :p_critical, :p_danger, :escalated, :scored_at)"""),
                record
            )
        
        servers = [
            {"server_id": "server1", "risk_tier": "high"},
            {"server_id": "server2", "risk_tier": "low"},
        ]
        
        for record in servers:
            db.execute(
                text("INSERT INTO mcp_server_registry (server_id, risk_tier) VALUES (:server_id, :risk_tier)"),
                record
            )
        
        db.commit()
    
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/api/scoring/summary")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    
    assert data["scored_servers"] >= 2, f"Expected scored_servers >= 2, got {data['scored_servers']}"
    assert len(data["axis_stats"]) >= 1, f"Expected axis_stats length >= 1, got {len(data['axis_stats'])}"
    
    print("PASS")