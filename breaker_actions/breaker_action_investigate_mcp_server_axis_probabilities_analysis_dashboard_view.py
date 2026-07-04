from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
from typing import List, Dict, Any
import requests
import json

app = FastAPI()

def get_mcp_server_axis_probabilities_analysis_dashboard_view_data(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Retrieve data for the MCP server axis probabilities analysis dashboard view.
    """
    # Get MCP server data
    mcp_servers = session.query(MCPServerRegistry).all()
    mcp_server_data = [{"id": server.id, "name": server.name} for server in mcp_servers]

    # Get MCP LLM axis scores
    axis_scores = session.query(MCPLLMAxisScores).all()
    axis_scores_data = [{"id": score.id, "server_id": score.server_id, "axis": score.axis, "score": score.score} for score in axis_scores]

    # Get MCP score disputes
    score_disputes = session.query(MCPScoreDisputes).all()
    score_disputes_data = [{"id": dispute.id, "score_id": dispute.score_id, "reason": dispute.reason} for dispute in score_disputes]

    # Get orgs and users data
    orgs = session.query(Orgs).all()
    orgs_data = [{"id": org.id, "name": org.name} for org in orgs]

    users = session.query(Users).all()
    users_data = [{"id": user.id, "name": user.name, "org_id": user.org_id} for user in users]

    # Get mesh memory data from ZoComputer store
    mesh_memory_data = []
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
        if response.status_code == 200:
            mesh_memory_data = response.json()
    except requests.RequestException:
        pass

    return {
        "mcp_servers": mcp_server_data,
        "axis_scores": axis_scores_data,
        "score_disputes": score_disputes_data,
        "orgs": orgs_data,
        "users": users_data,
        "mesh_memory": mesh_memory_data
    }

@app.get("/investigate/mcp_server_axis_probabilities_analysis_dashboard_view")
async def investigate_mcp_server_axis_probabilities_analysis_dashboard_view(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Investigate the MCP server axis probabilities analysis dashboard view.
    """
    try:
        data = get_mcp_server_axis_probabilities_analysis_dashboard_view_data(session)
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Mock data for self-test
    session = SessionLocal()
    session.add(MCPServerRegistry(id=1, name="Server 1"))
    session.add(MCPLLMAxisScores(id=1, server_id=1, axis="axis1", score=0.8))
    session.add(MCPScoreDisputes(id=1, score_id=1, reason="Dispute reason"))
    session.add(Orgs(id=1, name="Org 1"))
    session.add(Users(id=1, name="User 1", org_id=1))
    session.commit()

    # Test the endpoint
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/investigate/mcp_server_axis_probabilities_analysis_dashboard_view")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    print("PASS")