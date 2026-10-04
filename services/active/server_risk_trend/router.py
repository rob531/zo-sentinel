from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()

class ServerRiskTrendResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    confidence: float
    trend: str

@router.get("/server-risk-trend", response_model=List[ServerRiskTrendResponse])
def get_server_risk_trend(db: Session = Depends(get_session)):
    servers = db.query(McpServerRegistry).all()
    trends = []
    for server in servers:
        axis_scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server.server_id).all()
        trend = "stable"
        if len(axis_scores) > 1:
            if axis_scores[-1].risk_tier > axis_scores[-2].risk_tier:
                trend = "increasing"
            elif axis_scores[-1].risk_tier < axis_scores[-2].risk_tier:
                trend = "decreasing"
        trends.append({
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": server.risk_tier,
            "confidence": server.confidence,
            "trend": trend
        })
    return trends

if __name__ == "__main__":
    import pytest
    from fastapi.testclient import TestClient
    from app.main import app

    def override_get_session():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine("sqlite:///:memory:")
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        return SessionLocal()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    def test_get_server_risk_trend():
        response = client.get("/server-risk-trend")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    pytest.main(["-v", "__file__"])
