from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from .logic import analyze_risk_tier_trends

router = APIRouter(prefix="/api/risk/trend")

class Pattern(BaseModel):
    description: str
    affected_servers: List[int]

class Anomaly(BaseModel):
    description: str
    affected_servers: List[int]

class AnalysisResult(BaseModel):
    patterns: List[Pattern]
    anomalies: List[Anomaly]

@router.get("/analysis", response_model=AnalysisResult)
async def get_risk_tier_trend_analysis(days: int, session: Session = Depends(get_session)):
    try:
        analysis = analyze_risk_tier_trends(session, days)
        return {"analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        # Create test servers
        servers = [
            McpServerRegistry(id=i, hostname=f"server{i}", ip_address=f"192.168.1.{i}")
            for i in range(1, 6)
        ]
        session.add_all(servers)

        # Create test scores with tier changes
        from datetime import datetime, timedelta
        today = datetime.now().date()
        for day in range(3):
            date = today - timedelta(days=day)
            for i in range(1, 6):
                tier = (i + day) % 3 + 1  # Rotate tiers 1-3
                session.add(McpLlmAxisScore(
                    server_id=i,
                    date=date,
                    risk_tier=tier,
                    llm_axis_score=0.5 + (i * 0.1),
                    llm_axis_name="test_axis"
                ))
        session.commit()

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/risk/trend/analysis?days=3")
    assert response.status_code == 200
    assert len(response.json()["analysis"]["patterns"]) >= 1
    print("PASS")