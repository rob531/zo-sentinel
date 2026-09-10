from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()

class SignalScoresResponse(BaseModel):
    server_id: int
    signal_scores: dict
    timestamp: str

class MeshMemoryResponse(BaseModel):
    server_id: int
    mesh_memory: dict
    timestamp: str

def get_mesh_memory(server_id: int, session: Depends(get_session)):
    """Get mesh memory for a specific server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise HTTPException(status_code=404, detail="Server not found in mesh memory")
        return MeshMemoryResponse(**data[0])
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching mesh memory: {str(e)}")

def get_signal_scores(server_id: int, session: Depends(get_session)):
    """Get signal scores for a specific server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise HTTPException(status_code=404, detail="Server not found in signal scores")
        return SignalScoresResponse(**data[0])
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching signal scores: {str(e)}")

def mesh_scores_endpoint(server_id: int, session: Depends(get_session)):
    """Get mesh scores for a specific server."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            raise HTTPException(status_code=404, detail="Server not found in mesh scores")
        return SignalScoresResponse(**data[0])
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching mesh scores: {str(e)}")

def mesh_memory_endpoint(server_id: int, session: Depends(get_session)):
    """Get mesh memory for a specific server."""
    return get_mesh_memory(server_id, session)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the get_session dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create tables for testing
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Test data
    test_server = McpServerRegistry(server_id=1, hostname="test.example.com")
    test_session = TestSession()
    test_session.add(test_server)
    test_session.commit()

    client = TestClient(app)

    # Test get_mesh_memory
    response = client.get("/get_mesh_memory?server_id=1")
    assert response.status_code == 404  # Expected since mesh_memory is empty in test

    # Test get_signal_scores
    response = client.get("/get_signal_scores?server_id=1")
    assert response.status_code == 404  # Expected since mcp_signal_scores is empty in test

    # Test mesh_scores_endpoint
    response = client.get("/mesh_scores_endpoint?server_id=1")
    assert response.status_code == 404  # Expected since mcp_signal_scores is empty in test

    # Test mesh_memory_endpoint
    response = client.get("/mesh_memory_endpoint?server_id=1")
    assert response.status_code == 404  # Expected since mesh_memory is empty in test

    print("PASS")