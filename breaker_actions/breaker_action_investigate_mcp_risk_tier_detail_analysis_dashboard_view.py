from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPSignalScores, MCPScoreDisputes
from typing import List, Optional
import requests
from pydantic import BaseModel

app = FastAPI()

class RiskTierAnalysis(BaseModel):
    server_id: int
    risk_tier: str
    llm_axis_scores: dict
    signal_scores: dict
    disputes: List[dict]

class BreakerActionResponse(BaseModel):
    action: str
    status: str
    details: Optional[List[RiskTierAnalysis]]

def get_mesh_data(server_ids: List[int]) -> dict:
    """Query ZoComputer store for MESH/pipeline data"""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mcp_signal_scores WHERE server_id IN :server_ids",
              "params": {"server_ids": server_ids}}
    )
    return response.json() if response.status_code == 200 else {}

def get_llm_axis_scores(db: Session, server_ids: List[int]) -> dict:
    """Get LLM axis scores from app database"""
    return {
        score.server_id: {
            "axis": score.axis,
            "score": score.score,
            "timestamp": score.timestamp
        }
        for score in db.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id.in_(server_ids)).all()
    }

def get_score_disputes(db: Session, server_ids: List[int]) -> dict:
    """Get score disputes from app database"""
    return {
        dispute.server_id: {
            "dispute_id": dispute.id,
            "reason": dispute.reason,
            "status": dispute.status,
            "timestamp": dispute.timestamp
        }
        for dispute in db.query(MCPScoreDisputes).filter(MCPScoreDisputes.server_id.in_(server_ids)).all()
    }

def analyze_risk_tier(db: Session, server_id: int) -> RiskTierAnalysis:
    """Analyze risk tier for a single server"""
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        raise ValueError(f"Server {server_id} not found")

    llm_scores = get_llm_axis_scores(db, [server_id])
    signal_scores = get_mesh_data([server_id])
    disputes = get_score_disputes(db, [server_id])

    return RiskTierAnalysis(
        server_id=server_id,
        risk_tier=server.risk_tier,
        llm_axis_scores=llm_scores.get(server_id, {}),
        signal_scores=signal_scores.get(server_id, {}),
        disputes=disputes.get(server_id, [])
    )

@app.get("/investigate_mcp_risk_tier", response_model=BreakerActionResponse)
async def investigate_mcp_risk_tier(
    server_ids: List[int],
    db: Session = Depends(get_session)
) -> BreakerActionResponse:
    """Investigate MCP risk tier for given servers"""
    try:
        analyses = []
        for server_id in server_ids:
            try:
                analyses.append(analyze_risk_tier(db, server_id))
            except Exception as e:
                print(f"Error analyzing server {server_id}: {str(e)}")

        return BreakerActionResponse(
            action="investigate_mcp_risk_tier",
            status="success",
            details=analyses
        )
    except Exception as e:
        return BreakerActionResponse(
            action="investigate_mcp_risk_tier",
            status="error",
            details=str(e)
        )

if __name__ == "__main__":
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.dependency_overrides import dependency_overrides

    # Setup test database
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_db = SessionLocal()

    # Override dependencies for testing
    dependency_overrides[get_session] = lambda: test_db

    # Test data
    test_server = MCPServerRegistry(id=1, risk_tier="high")
    test_llm_score = MCPLLMAxisScores(server_id=1, axis="axis1", score=0.8, timestamp="2026-07-03T18:00:00")
    test_signal_score = {"server_id": 1, "signal": "signal1", "score": 0.9}
    test_dispute = MCPScoreDisputes(server_id=1, reason="test", status="open", timestamp="2026-07-03T18:00:00")

    test_db.add_all([test_server, test_llm_score, test_dispute])
    test_db.commit()

    # Mock mesh data
    def mock_get_mesh_data(server_ids):
        return {1: test_signal_score}

    # Override mesh data function for testing
    original_get_mesh_data = get_mesh_data
    get_mesh_data = mock_get_mesh_data

    # Run test
    try:
        response = investigate_mcp_risk_tier([1])
        if response.status == "success" and len(response.details) == 1:
            print("PASS")
        else:
            print("FAIL")
    except Exception as e:
        print(f"FAIL: {str(e)}")
    finally:
        # Restore original functions
        get_mesh_data = original_get_mesh_data
        test_db.close()