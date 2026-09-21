from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from typing import List

from app.db import get_session

router = APIRouter()


class TierCount(BaseModel):
    risk_tier: str
    count: int


class OrgRiskResponse(BaseModel):
    org_id: str
    days: int
    tiers: List[TierCount]


@router.get("/api/organization/{org_id}/risk_tiers", response_model=OrgRiskResponse)
def get_org_risk_tiers(org_id: str, days: int = 30, session: Session = Depends(get_session)) -> OrgRiskResponse:
    query = text("""
        SELECT 
            r.risk_tier,
            COUNT(*) as count
        FROM McpServerRegistry r
        WHERE r.org_id = :org_id
          AND r.last_assessed >= datetime('now', '-' || :days || ' days')
        GROUP BY r.risk_tier
    """)
    
    result = session.execute(query, {"org_id": org_id, "days": days})
    rows = result.fetchall()
    
    tiers = [TierCount(risk_tier=row[0], count=row[1]) for row in rows]
    
    return OrgRiskResponse(org_id=org_id, days=days, tiers=tiers)


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:")
    
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                org_id TEXT NOT NULL,
                risk_tier TEXT NOT NULL,
                last_assessed TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE McpLlmAxisScore (
                server_id TEXT PRIMARY KEY,
                axis_name TEXT NOT NULL,
                p_top REAL NOT NULL,
                scored_at TIMESTAMP NOT NULL
            )
        """))
        
        from datetime import datetime, timedelta
        now = datetime.now()
        ten_days_ago = now - timedelta(days=10)
        
        servers = [
            ("srv1", "org123", "HIGH_RISK_ISOLATED", ten_days_ago.isoformat()),
            ("srv2", "org123", "CAUTION_LIMITED", (ten_days_ago + timedelta(days=2)).isoformat()),
            ("srv3", "org123", "HIGH_RISK_ISOLATED", (ten_days_ago + timedelta(days=5)).isoformat()),
        ]
        
        for server_id, org_id, risk_tier, last_assessed in servers:
            conn.execute(text(
                "INSERT INTO McpServerRegistry (server_id, org_id, risk_tier, last_assessed) VALUES (:s, :o, :r, :l)"
            ), {"s": server_id, "o": org_id, "r": risk_tier, "l": last_assessed})
            conn.execute(text(
                "INSERT INTO McpLlmAxisScore (server_id, axis_name, p_top, scored_at) VALUES (:s, :a, :p, :l)"
            ), {"s": server_id, "a": "test_axis", "p": 0.5, "l": last_assessed})
    
    TestingSession = sessionmaker(bind=engine)
    
    app = FastAPI()
    app.include_router(router)
    
    from fastapi.testclient import TestClient
    
    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    response = client.get("/api/organization/org123/risk_tiers?days=30")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["org_id"] == "org123"
    assert data["days"] == 30
    
    tier_counts = {t["risk_tier"]: t["count"] for t in data["tiers"]}
    assert tier_counts.get("HIGH_RISK_ISOLATED") == 2, f"Expected 2, got {tier_counts}"
    assert tier_counts.get("CAUTION_LIMITED") == 1, f"Expected 1, got {tier_counts}"
    
    print("PASS")