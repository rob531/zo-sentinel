from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import List
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
import random

router = APIRouter(prefix="/api", tags=["risk_tier_transition_report"])


class TransitionEntry(BaseModel):
    date: str
    from_tier: str
    to_tier: str
    count: int


class TransitionsResponse(BaseModel):
    days: int
    series: List[TransitionEntry]


@router.get("/risk/transitions", response_model=TransitionsResponse)
def get_transitions(days: int = 7, session: Session = Depends(get_session)):
    start_date = datetime.now() - timedelta(days=days)
    sql = text("""
        SELECT 
            DATE(llm.scored_at) as transition_date,
            sr.risk_tier as to_tier,
            llm.escalated_to as from_tier,
            COUNT(*) as count
        FROM McpLlmAxisScore llm
        JOIN McpServerRegistry sr ON llm.server_id = sr.server_id
        WHERE llm.escalated = true
          AND llm.scored_at >= :start_date
          AND llm.escalated_to IS NOT NULL
          AND sr.risk_tier IS NOT NULL
        GROUP BY DATE(llm.scored_at), llm.escalated_to, sr.risk_tier
        ORDER BY transition_date, from_tier, to_tier
    """)
    result = session.execute(sql, {"start_date": start_date})
    series = [
        TransitionEntry(
            date=row.transition_date.isoformat() if hasattr(row.transition_date, 'isoformat') else str(row.transition_date),
            from_tier=row.from_tier,
            to_tier=row.to_tier,
            count=row.count
        )
        for row in result
    ]
    return TransitionsResponse(days=days, series=series)


if __name__ == "__main__":
    from fastapi import FastAPI
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def create_tables():
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE McpServerRegistry (
                    server_id INTEGER PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT,
                    url TEXT,
                    trust_score REAL,
                    confidence REAL,
                    description TEXT,
                    registry_source TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    meta TEXT,
                    scan_count INTEGER,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    last_scanned TIMESTAMP,
                    last_assessed TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE McpLlmAxisScore (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    probs TEXT,
                    p_critical REAL,
                    p_danger REAL,
                    p_top REAL,
                    model_version TEXT,
                    decision_rule_version TEXT,
                    adapter_sha256 TEXT,
                    escalated INTEGER,
                    escalated_to TEXT,
                    scored_at TIMESTAMP
                )
            """))
            conn.commit()

    def seed_data():
        session = TestingSessionLocal()
        now = datetime.now()
        
        session.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, url, trust_score, confidence, description, registry_source, verdict, verdict_reasoning, meta, scan_count, first_seen, last_seen, last_scanned, last_assessed)
            VALUES 
            (1, 'server-alpha', 'tier_2', 'https://alpha.example.com', 0.85, 0.9, 'Alpha server', 'internal', 'approved', 'good', '{}', 10, :now, :now, :now, :now),
            (2, 'server-beta', 'tier_3', 'https://beta.example.com', 0.65, 0.7, 'Beta server', 'internal', 'approved', 'ok', '{}', 5, :now, :now, :now, :now),
            (3, 'server-gamma', 'tier_1', 'https://gamma.example.com', 0.95, 0.95, 'Gamma server', 'internal', 'approved', 'excellent', '{}', 20, :now, :now, :now, :now)
        """), {"now": now})
        
        day1 = now - timedelta(days=1)
        day2 = now - timedelta(days=2)
        
        session.execute(text("""
            INSERT INTO McpLlmAxisScore (server_id, axis_name, label, label_index, probs, p_critical, p_danger, p_top, model_version, decision_rule_version, adapter_sha256, escalated, escalated_to, scored_at)
            VALUES 
            (1, 'risk', 'high', 2, '[0.1,0.2,0.7]', 0.1, 0.7, 0.3, 'v1', 'r1', 'sha1', 1, 'tier_2', :day1),
            (1, 'risk', 'high', 2, '[0.1,0.2,0.7]', 0.1, 0.7, 0.3, 'v1', 'r1', 'sha1', 1, 'tier_2', :day1),
            (1, 'risk', 'high', 2, '[0.1,0.2,0.7]', 0.1, 0.7, 0.3, 'v1', 'r1', 'sha1', 1, 'tier_2', :day1),
            (2, 'risk', 'critical', 3, '[0.1,0.3,0.6]', 0.6, 0.3, 0.1, 'v1', 'r1', 'sha2', 1, 'tier_3', :day2),
            (2, 'risk', 'critical', 3, '[0.1,0.3,0.6]', 0.6, 0.3, 0.1, 'v1', 'r1', 'sha2', 1, 'tier_3', :day2),
            (3, 'risk', 'low', 0, '[0.7,0.2,0.1]', 0.7, 0.2, 0.1, 'v1', 'r1', 'sha3', 1, 'tier_1', :day2),
            (3, 'risk', 'low', 0, '[0.7,0.2,0.1]', 0.7, 0.2, 0.1, 'v1', 'r1', 'sha3', 1, 'tier_1', :day2),
            (3, 'risk', 'low', 0, '[0.7,0.2,0.1]', 0.7, 0.2, 0.1, 'v1', 'r1', 'sha3', 1, 'tier_1', :day2),
            (3, 'risk', 'low', 0, '[0.7,0.2,0.1]', 0.7, 0.2, 0.1, 'v1', 'r1', 'sha3', 1, 'tier_1', :day2)
        """), {"day1": day1, "day2": day2})
        
        session.commit()
        session.close()

    create_tables()
    seed_data()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)
    
    response = client.get("/api/risk/transitions?days=5")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert len(data["series"]) == 3, f"Expected 3 series entries, got {len(data['series'])}"
    
    counts = [entry["count"] for entry in data["series"]]
    assert 4 in counts, f"Expected count of 4 in {counts}"
    
    print("PASS")