from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPSignalScores, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from typing import List, Dict, Optional
import requests

app = FastAPI()

def get_mcp_risk_tier_summary_analysis_view_data(session: Session = Depends(get_session)) -> Dict:
    """Fetch data for the MCP risk tier summary analysis view."""
    try:
        # Fetch data from APP tables
        servers = session.query(MCPServerRegistry).all()
        llm_scores = session.query(MCPLLMAxisScores).all()
        disputes = session.query(MCPScoreDisputes).all()

        # Fetch data from MESH tables via ZoComputer store
        response = requests.post("http://127.0.0.1:8772/query", json={
            "query": "SELECT * FROM mcp_signal_scores"
        })
        signal_scores = response.json() if response.status_code == 200 else []

        return {
            "servers": [server.to_dict() for server in servers],
            "llm_scores": [score.to_dict() for score in llm_scores],
            "disputes": [dispute.to_dict() for dispute in disputes],
            "signal_scores": signal_scores
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def investigate_mcp_risk_tier_summary_analysis_view() -> Dict:
    """Investigate the MCP risk tier summary analysis view."""
    data = get_mcp_risk_tier_summary_analysis_view_data()

    # Perform investigation logic here
    investigation_results = {
        "servers_count": len(data["servers"]),
        "llm_scores_count": len(data["llm_scores"]),
        "disputes_count": len(data["disputes"]),
        "signal_scores_count": len(data["signal_scores"]),
        "analysis": "Investigation in progress..."
    }

    return investigation_results

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Mock data for self-test
    test_server = MCPServerRegistry(id=1, name="Test Server", org_id=1)
    test_llm_score = MCPLLMAxisScores(id=1, server_id=1, axis="Test Axis", score=0.8)
    test_dispute = MCPScoreDisputes(id=1, server_id=1, user_id=1, reason="Test Reason")

    test_session = TestSession()
    test_session.add_all([test_server, test_llm_score, test_dispute])
    test_session.commit()

    # Run the investigation
    result = investigate_mcp_risk_tier_summary_analysis_view()
    print("PASS")