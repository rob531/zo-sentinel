from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import List
from datetime import date, timedelta
from collections import defaultdict

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


# Pydantic models
class DriftDataPoint(BaseModel):
    date: date
    servers_scored: int
    mean_p_top: float
    escalated_servers: int
    axis_coverage: float


class DriftResponse(BaseModel):
    days: int
    drift_series: List[DriftDataPoint]


# FastAPI app
app = FastAPI()


@app.get("/api/scoring/drift", response_model=DriftResponse)
def get_scoring_drift(days: int = 7, session: Session = Depends(get_session)):
    """Compute per-day drift statistics across all scored servers."""
    start_date = date.today() - timedelta(days=days)
    
    # Query axis scores joined with server registry, grouped by date
    query = text("""
        SELECT 
            DATE(s.scored_at) as scored_date,
            s.server_id,
            s.p_top,
            s.escalated,
            s.axis_name,
            r.server_id as r_server_id
        FROM mcp_llm_axis_scores s
        LEFT JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE DATE(s.scored_at) >= :start_date
        ORDER BY scored_date
    """)
    
    result = session.execute(query, {"start_date": start_date.isoformat()})
    rows = result.fetchall()
    
    # Aggregate by date
    daily_data = defaultdict(lambda: {
        "servers": set(),
        "p_tops": [],
        "escalated_servers": set(),
        "axis_count": 0
    })
    
    for row in rows:
        scored_date = row.scored_date
        if isinstance(scored_date, str):
            scored_date = date.fromisoformat(scored_date)
        
        daily_data[scored_date]["servers"].add(row.server_id)
        if row.p_top is not None:
            daily_data[scored_date]["p_tops"].append(row.p_top)
        if row.escalated:
            daily_data[scored_date]["escalated_servers"].add(row.server_id)
        daily_data[scored_date]["axis_count"] += 1
    
    # Build drift series
    drift_series = []
    for scored_date in sorted(daily_data.keys()):
        data = daily_data[scored_date]
        servers_scored = len(data["servers"])
        mean_p_top = sum(data["p_tops"]) / len(data["p_tops"]) if data["p_tops"] else 0.0
        escalated_servers = len(data["escalated_servers"])
        axis_coverage = data["axis_count"] / servers_scored if servers_scored > 0 else 0.0
        
        drift_series.append(DriftDataPoint(
            date=scored_date,
            servers_scored=servers_scored,
            mean_p_top=round(mean_p_top, 4),
            escalated_servers=escalated_servers,
            axis_coverage=round(axis_coverage, 4)
        ))
    
    return DriftResponse(days=days, drift_series=drift_series)


# Self-test
if __name__ == "__main__":
    # Create in-memory SQLite engine for testing
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    # Create tables
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT NOT NULL,
                axis_name TEXT NOT NULL,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                escalated INTEGER DEFAULT 0,
                escalated_to TEXT,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                scored_at TIMESTAMP NOT NULL
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
                scan_count INTEGER DEFAULT 0,
                meta TEXT
            )
        """))
        conn.commit()
    
    # Create session
    TestingSessionLocal = sessionmaker(bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Seed test data: 3 servers with scores across 3 days
    base_date = date.today()
    test_servers = [
        ("srv_001", "Server Alpha"),
        ("srv_002", "Server Beta"),
        ("srv_003", "Server Gamma"),
    ]
    
    with engine.connect() as conn:
        # Insert servers
        for server_id, name in test_servers:
            conn.execute(text("""
                INSERT INTO mcp_server_registry (server_id, name)
                VALUES (:server_id, :name)
            """), {"server_id": server_id, "name": name})
        
        # Insert axis scores for 3 days
        for day_offset in range(3):
            scored_date = base_date - timedelta(days=2 - day_offset)
            for server_id, _ in test_servers:
                # 2-3 axis scores per server per day
                for axis_idx in range(2 + (day_offset % 2)):
                    p_top_val = 0.5 + (axis_idx * 0.1) + (day_offset * 0.05)
                    escalated = 1 if p_top_val > 0.75 else 0
                    conn.execute(text("""
                        INSERT INTO mcp_llm_axis_scores 
                        (server_id, axis_name, p_top, escalated, scored_at)
                        VALUES (:server_id, :axis_name, :p_top, :escalated, :scored_at)
                    """), {
                        "server_id": server_id,
                        "axis_name": f"axis_{axis_idx}",
                        "p_top": p_top_val,
                        "escalated": escalated,
                        "scored_at": f"{scored_date.isoformat()} 12:00:00"
                    })
        
        conn.commit()
    
    # Override dependency and run test
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    
    response = client.get("/api/scoring/drift?days=3")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert data["days"] == 3, f"Expected days=3, got {data['days']}"
    
    drift_series = data["drift_series"]
    assert len(drift_series) == 3, f"Expected 3 data points, got {len(drift_series)}"
    
    # Validate each data point
    for point in drift_series:
        assert point["servers_scored"] == 3, f"Expected 3 servers, got {point['servers_scored']}"
        assert 0.0 <= point["mean_p_top"] <= 1.0, f"mean_p_top out of range: {point['mean_p_top']}"
        assert point["axis_coverage"] > 0, f"axis_coverage should be > 0"
    
    print("PASS")