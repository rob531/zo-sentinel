from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

class SignalScoresRequest(BaseModel):
    server_ids: List[int]
    org_id: int

class SignalScoresResponse(BaseModel):
    server_id: int
    scores: dict
    last_updated: Optional[str] = None

class MeshMemoryRequest(BaseModel):
    server_ids: List[int]

class MeshMemoryResponse(BaseModel):
    server_id: int
    memory: dict

def get_signal_scores(server_ids: List[int], org_id: int, session=Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN {server_ids} AND org_id = {org_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory(server_ids: List[int], session=Depends(get_session)):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id IN {server_ids}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/signal_scores", response_model=List[SignalScoresResponse])
async def signal_scores_endpoint(request: SignalScoresRequest, session=Depends(get_session)):
    scores = get_signal_scores(request.server_ids, request.org_id, session)
    return [SignalScoresResponse(**score) for score in scores]

@app.post("/mesh_memory", response_model=List[MeshMemoryResponse])
async def mesh_memory_endpoint(request: MeshMemoryRequest, session=Depends(get_session)):
    memory = get_mesh_memory(request.server_ids, session)
    return [MeshMemoryResponse(**mem) for mem in memory]

def _run_self_test():
    from app.dependency_overrides import dependency_overrides
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Test data
    test_server = McpServerRegistry(server_id=1, org_id=1)
    test_score = McpLlmAxisScore(server_id=1, org_id=1, scores={"test": 1.0}, last_updated="2023-01-01")
    test_memory = {"test": "value"}

    # Add test data
    session = SessionLocal()
    session.add(test_server)
    session.add(test_score)
    session.commit()

    # Mock the mesh_memory table
    def mock_get_mesh_memory(server_ids):
        return [{"server_id": 1, "memory": test_memory}]

    dependency_overrides[get_mesh_memory] = mock_get_mesh_memory

    # Test signal_scores_endpoint
    response = signal_scores_endpoint(SignalScoresRequest(server_ids=[1], org_id=1))
    assert len(response) == 1
    assert response[0].server_id == 1
    assert response[0].scores == {"test": 1.0}
    assert response[0].last_updated == "2023-01-01"

    # Test mesh_memory_endpoint
    response = mesh_memory_endpoint(MeshMemoryRequest(server_ids=[1]))
    assert len(response) == 1
    assert response[0].server_id == 1
    assert response[0].memory == test_memory

    print("PASS")

if __name__ == "__main__":
    _run_self_test()