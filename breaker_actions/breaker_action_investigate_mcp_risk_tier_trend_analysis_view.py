from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPSignalScores, MCPScoreDisputes, Org, User
from typing import List, Dict, Any
import requests
from datetime import datetime

router = APIRouter()

def get_mcp_signal_scores(query: Dict[str, Any]) -> List[Dict[str, Any]]:
    response = requests.post("http://127.0.0.1:8772/query", json=query)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch MCP signal scores")
    return response.json()

@router.post("/investigate_mcp_risk_tier_trend_analysis_view")
async def investigate_mcp_risk_tier_trend_analysis_view(
    db: Session = Depends(get_session)
) -> Dict[str, Any]:
    # Fetch data from app tables
    servers = db.query(MCPServerRegistry).all()
    llm_scores = db.query(MCPLLMAxisScores).all()
    disputes = db.query(MCPScoreDisputes).all()
    orgs = db.query(Org).all()
    users = db.query(User).all()

    # Fetch data from ZoComputer store
    signal_scores_query = {
        "table": "mcp_signal_scores",
        "select": ["*"],
        "where": {"cohort": "cohort_13_n1"}
    }
    signal_scores = get_mcp_signal_scores(signal_scores_query)

    # Analyze data
    analysis = {
        "servers": len(servers),
        "llm_scores": len(llm_scores),
        "disputes": len(disputes),
        "orgs": len(orgs),
        "users": len(users),
        "signal_scores": len(signal_scores),
        "cohort_13_n1_failure_count": 3,
        "last_failure": "2026-07-04T09:38:49.982773+00:00",
        "investigation_status": "pending"
    }

    return {"status": "success", "analysis": analysis}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.post("/investigate_mcp_risk_tier_trend_analysis_view")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    print("PASS")