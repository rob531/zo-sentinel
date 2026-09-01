from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import List
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk"])


class TrendEntry(BaseModel):
    date: str
    tier: str
    count: int


class TrendsResponse(BaseModel):
    days: int
    series: List[TrendEntry]


def get_risk_tier_trends(days: int, db: Session) -> TrendsResponse:
    query = text("""
        SELECT 
            DATE(s.created_at) as trend_date,
            s.risk_tier,
            COUNT(*) as transition_count
        FROM McpLlmAxisScore s
        JOIN McpServerRegistry r ON s.server_id = r.id
        WHERE s.created_at >= DATE('now', :days_param || ' days')
        GROUP BY DATE(s.created_at), s.risk_tier
        ORDER BY trend_date, s.risk_tier
    """)
    result = db.execute(query, {"days_param": -days})
    rows = result.fetchall()
    
    series = [TrendEntry(date=str(row[0]), tier=row[1], count=row[2]) for row in rows]
    return TrendsResponse(days=days, series=series)


@router.get("/risk/trends", response_model=TrendsResponse)
def get_trends(days: int = 30, db: Session = Depends(get_session)):
    return get_risk_tier_trends(days, db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    SessionLocal = sessionmaker(bind=test_engine)
    
    with test_engine.connect() as conn:
        conn.execute(text("CREATE TABLE McpServerRegistry (id INTEGER PRIMARY KEY, name TEXT, risk_tier TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"))
        conn.execute(text("CREATE TABLE McpLlmAxisScore (id INTEGER PRIMARY KEY, server_id INTEGER, risk_tier TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"))
        conn.commit()
    
    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    session = SessionLocal()
    
    session.execute(text("INSERT INTO McpServerRegistry (id, name, risk_tier) VALUES (1, 'server_a', 'low')"))
    session.execute(text("INSERT INTO McpServerRegistry (id, name, risk_tier) VALUES (2, 'server_b', 'medium')"))
    session.execute(text("INSERT INTO McpServerRegistry (id, name, risk_tier) VALUES (3, 'server_c', 'high')"))
    
    session.execute(text("INSERT INTO McpLlmAxisScore (server_id, risk_tier, created_at) VALUES (1, 'low', datetime('now', '-1 day'))"))
    session.execute(text("INSERT INTO McpLlmAxisScore (server_id, risk_tier, created_at) VALUES (1, 'medium', datetime('now', '-1 day'))"))
    session.execute(text("INSERT INTO McpLlmAxisScore (server_id, risk_tier, created_at) VALUES (2, 'medium', datetime('now', '-1 day'))"))
    session.execute(text("INSERT INTO McpLlmAxisScore (server_id, risk_tier, created_at) VALUES (2, 'high', datetime('now'))"))
    session.execute(text("INSERT INTO McpLlmAxisScore (server_id, risk_tier, created_at) VALUES (3, 'high', datetime('now'))"))
    session.execute(text("INSERT INTO McpLlmAxisScore (server_id, risk_tier, created_at) VALUES (3, 'critical', datetime('now'))"))
    session.commit()
    session.close()
    
    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session
    
    from fastapi.testclient import TestClient
    client = TestClient(that_app)
    
    response = client.get("/api/risk/trends?days=2")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    series = data.get("series", [])
    
    assert len(series) == 4, f"Expected 4 entries, got {len(series)}"
    
    counts = {entry["tier"]: entry["count"] for entry in series}
    assert counts.get("medium") == 2 or counts.get("high") == 2, f"Expected a tier with count 2, got {counts}"
    
    print("PASS")
    sys.exit(0)