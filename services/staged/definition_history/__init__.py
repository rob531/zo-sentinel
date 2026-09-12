from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from pydantic import BaseModel
import requests
from datetime import datetime

router = APIRouter()

class SignalScoresResponse(BaseModel):
    server_id: int
    signal_scores: dict
    last_updated: datetime

class MeshMemoryResponse(BaseModel):
    server_id: int
    mesh_memory: dict
    last_updated: datetime

class ScoreDisputesResponse(BaseModel):
    dispute_id: int
    server_id: int
    disputed_score: float
    reason: str
    status: str
    created_at: datetime

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> Optional[SignalScoresResponse]:
    """Get signal scores for a specific server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    data = response.json()
    if not data:
        return None
    return SignalScoresResponse(**data[0])

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> Optional[MeshMemoryResponse]:
    """Get mesh memory for a specific server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    data = response.json()
    if not data:
        return None
    return MeshMemoryResponse(**data[0])

def get_score_disputes(server_id: int, db: Session = Depends(get_session)) -> List[ScoreDisputesResponse]:
    """Get score disputes for a specific server from the app database."""
    disputes = db.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()
    return [ScoreDisputesResponse(**dispute.__dict__) for dispute in disputes]

@router.get("/signal_scores/{server_id}", response_model=SignalScoresResponse)
async def signal_scores_endpoint(server_id: int, db: Session = Depends(get_session)):
    """Endpoint to get signal scores for a specific server."""
    return get_signal_scores(server_id, db)

@router.get("/mesh_memory/{server_id}", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint(server_id: int, db: Session = Depends(get_session)):
    """Endpoint to get mesh memory for a specific server."""
    return get_mesh_memory(server_id, db)

@router.get("/score_disputes/{server_id}", response_model=List[ScoreDisputesResponse])
async def get_score_disputes_endpoint(server_id: int, db: Session = Depends(get_session)):
    """Endpoint to get score disputes for a specific server."""
    return get_score_disputes(server_id, db)

def _run_self_test():
    """Self-test for the module."""
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test data
    test_server = McpServerRegistry(id=1, hostname="test-server")
    test_session = TestSession()
    test_session.add(test_server)
    test_session.commit()

    # Test get_signal_scores
    try:
        response = get_signal_scores(1)
        if response is None:
            print("PASS: get_signal_scores returned None for non-existent server")
        else:
            print("FAIL: get_signal_scores did not return None for non-existent server")
    except Exception as e:
        print(f"FAIL: get_signal_scores raised an exception: {e}")

    # Test get_mesh_memory
    try:
        response = get_mesh_memory(1)
        if response is None:
            print("PASS: get_mesh_memory returned None for non-existent server")
        else:
            print("FAIL: get_mesh_memory did not return None for non-existent server")
    except Exception as e:
        print(f"FAIL: get_mesh_memory raised an exception: {e}")

    # Test get_score_disputes
    try:
        response = get_score_disputes(1)
        if isinstance(response, list) and len(response) == 0:
            print("PASS: get_score_disputes returned empty list for server with no disputes")
        else:
            print("FAIL: get_score_disputes did not return empty list for server with no disputes")
    except Exception as e:
        print(f"FAIL: get_score_disputes raised an exception: {e}")

    # Clean up
    app.dependency_overrides.clear()
    test_session.close()
    print("PASS")

if __name__ == "__main__":
    _run_self_test()