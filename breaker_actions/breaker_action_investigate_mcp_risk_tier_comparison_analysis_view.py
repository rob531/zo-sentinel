from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPSignalScores, MCPLLMAxisScores, MCPScoreDisputes
from typing import List, Dict, Optional
import requests
from datetime import datetime

app = FastAPI()

def get_mcp_signal_scores(server_id: int, session: Session = Depends(get_session)) -> List[Dict]:
    """Fetch MCP signal scores for a given server from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": "SELECT * FROM mcp_signal_scores WHERE server_id = :server_id",
            "params": {"server_id": server_id}
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch MCP signal scores")
    return response.json()

def get_mcp_llm_axis_scores(server_id: int, session: Session = Depends(get_session)) -> List[Dict]:
    """Fetch MCP LLM axis scores for a given server from the app database."""
    return session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()

def get_mcp_score_disputes(server_id: int, session: Session = Depends(get_session)) -> List[Dict]:
    """Fetch MCP score disputes for a given server from the app database."""
    return session.query(MCPScoreDisputes).filter(MCPScoreDisputes.server_id == server_id).all()

def get_server_details(server_id: int, session: Session = Depends(get_session)) -> Optional[Dict]:
    """Fetch server details from the app database."""
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        return None
    return {
        "id": server.id,
        "name": server.name,
        "risk_tier": server.risk_tier,
        "last_updated": server.last_updated
    }

def analyze_risk_tier_comparison(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Analyze risk tier comparison for a given server."""
    server_details = get_server_details(server_id, session)
    if not server_details:
        raise HTTPException(status_code=404, detail="Server not found")

    signal_scores = get_mcp_signal_scores(server_id, session)
    llm_axis_scores = get_mcp_llm_axis_scores(server_id, session)
    score_disputes = get_mcp_score_disputes(server_id, session)

    analysis = {
        "server_id": server_details["id"],
        "server_name": server_details["name"],
        "current_risk_tier": server_details["risk_tier"],
        "last_updated": server_details["last_updated"],
        "signal_scores": signal_scores,
        "llm_axis_scores": llm_axis_scores,
        "score_disputes": score_disputes,
        "analysis_timestamp": datetime.utcnow().isoformat()
    }

    return analysis

@app.post("/investigate_mcp_risk_tier_comparison")
async def investigate_mcp_risk_tier_comparison(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Investigate MCP risk tier comparison for a given server."""
    try:
        analysis = analyze_risk_tier_comparison(server_id, session)
        return {"status": "success", "data": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Mock data for self-test
    test_server = MCPServerRegistry(
        id=1,
        name="Test Server",
        risk_tier="High",
        last_updated=datetime.utcnow()
    )
    test_llm_score = MCPLLMAxisScores(
        server_id=1,
        axis="Security",
        score=0.85
    )
    test_dispute = MCPScoreDisputes(
        server_id=1,
        dispute_reason="Incorrect risk assessment"
    )

    with TestSession() as session:
        session.add(test_server)
        session.add(test_llm_score)
        session.add(test_dispute)
        session.commit()

    # Self-test
    try:
        response = app.client.post("/investigate_mcp_risk_tier_comparison", json={"server_id": 1})
        if response.status_code == 200:
            print("PASS")
        else:
            print("FAIL")
    except Exception as e:
        print("FAIL")