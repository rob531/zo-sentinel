from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from pydantic import BaseModel

app = FastAPI()

class SignalScoresResponse(BaseModel):
    server_id: int
    scores: Dict[str, float]

class MeshMemoryResponse(BaseModel):
    server_id: int
    memory: Dict[str, float]

class ScoreDisputesResponse(BaseModel):
    server_id: int
    disputes: List[Dict[str, str]]

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> MeshMemoryResponse:
    """Get mesh memory for a specific server."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    data = response.json()
    if not data:
        raise HTTPException(status_code=404, detail="Server not found")
    return MeshMemoryResponse(server_id=server_id, memory=data[0])

def signal_scores_endpoint(server_id: int, session: Session = Depends(get_session)) -> SignalScoresResponse:
    """Get signal scores for a specific server."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    data = response.json()
    if not data:
        raise HTTPException(status_code=404, detail="Server not found")
    return SignalScoresResponse(server_id=server_id, scores=data[0])

def mesh_scores_endpoint(server_id: int, session: Session = Depends(get_session)) -> MeshMemoryResponse:
    """Get mesh scores for a specific server."""
    return get_mesh_memory(server_id, session)

def get_score_disputes(server_id: int, session: Session = Depends(get_session)) -> ScoreDisputesResponse:
    """Get score disputes for a specific server."""
    disputes = session.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()
    return ScoreDisputesResponse(server_id=server_id, disputes=[dispute.__dict__ for dispute in disputes])

def _run_self_test():
    """Self-test for the service."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data
    test_server_id = 1

    # Test get_mesh_memory
    try:
        get_mesh_memory(test_server_id)
    except HTTPException as e:
        if e.status_code != 404:
            print("FAIL: get_mesh_memory did not return 404 for non-existent server")
            return

    # Test signal_scores_endpoint
    try:
        signal_scores_endpoint(test_server_id)
    except HTTPException as e:
        if e.status_code != 404:
            print("FAIL: signal_scores_endpoint did not return 404 for non-existent server")
            return

    # Test mesh_scores_endpoint
    try:
        mesh_scores_endpoint(test_server_id)
    except HTTPException as e:
        if e.status_code != 404:
            print("FAIL: mesh_scores_endpoint did not return 404 for non-existent server")
            return

    # Test get_score_disputes
    disputes = get_score_disputes(test_server_id)
    if disputes.server_id != test_server_id:
        print("FAIL: get_score_disputes returned incorrect server_id")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()