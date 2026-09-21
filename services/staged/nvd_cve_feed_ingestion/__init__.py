from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from pydantic import BaseModel
import json

router = APIRouter()

class SignalScoresResponse(BaseModel):
    server_id: int
    scores: dict
    last_updated: str

class MeshMemoryResponse(BaseModel):
    server_id: int
    memory: dict
    last_updated: str

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[SignalScoresResponse]:
    """Fetch signal scores for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        return None
    data = response.json()
    if not data:
        return None
    return SignalScoresResponse(**data[0])

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Optional[MeshMemoryResponse]:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        return None
    data = response.json()
    if not data:
        return None
    return MeshMemoryResponse(**data[0])

def mesh_scores_endpoint(server_id: int, session: Session = Depends(get_session)) -> dict:
    """Endpoint to fetch mesh scores for a given server."""
    scores = get_signal_scores(server_id, session)
    memory = get_mesh_memory(server_id, session)
    if not scores or not memory:
        raise HTTPException(status_code=404, detail="Server not found")
    return {
        "server_id": server_id,
        "scores": scores.scores,
        "memory": memory.memory,
        "last_updated": scores.last_updated
    }

def _run_self_test():
    """Self-test for the module."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data
    test_server_id = 1

    # Mock the ZoComputer store response
    def mock_query(query):
        if "mcp_signal_scores" in query:
            return [{"server_id": test_server_id, "scores": {"risk": 0.5}, "last_updated": "2023-01-01"}]
        elif "mesh_memory" in query:
            return [{"server_id": test_server_id, "memory": {"key": "value"}, "last_updated": "2023-01-01"}]
        return []

    original_post = requests.post
    requests.post = lambda url, json: original_post(url, json) if "127.0.0.1:8772" not in url else json

    try:
        scores = get_signal_scores(test_server_id)
        memory = get_mesh_memory(test_server_id)
        if not scores or not memory:
            print("FAIL")
            return
        result = mesh_scores_endpoint(test_server_id)
        if result["server_id"] != test_server_id:
            print("FAIL")
            return
        print("PASS")
    finally:
        app.dependency_overrides.pop(get_session)
        requests.post = original_post

if __name__ == "__main__":
    _run_self_test()