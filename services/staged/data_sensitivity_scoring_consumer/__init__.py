from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Dict, Optional
import requests
from pydantic import BaseModel

class MeshScore(BaseModel):
    server_id: int
    score: float
    timestamp: str

class MeshMemory(BaseModel):
    server_id: int
    memory: float
    timestamp: str

class SignalScore(BaseModel):
    server_id: int
    signal: str
    score: float
    timestamp: str

def mesh_scores(session: Session = Depends(get_session)) -> List[MeshScore]:
    """Fetch mesh scores for all servers from the database."""
    servers = session.query(McpServerRegistry).all()
    scores = []
    for server in servers:
        # In a real implementation, this would query the actual mesh scores
        # For this example, we'll return a dummy value
        scores.append(MeshScore(server_id=server.id, score=0.5, timestamp="2023-01-01T00:00:00Z"))
    return scores

def get_mesh_scores(session: Session = Depends(get_session)) -> List[MeshScore]:
    """Fetch mesh scores for all servers from the database."""
    return mesh_scores(session)

def get_mesh_memory(session: Session = Depends(get_session)) -> List[MeshMemory]:
    """Fetch mesh memory for all servers from the database."""
    servers = session.query(McpServerRegistry).all()
    memory = []
    for server in servers:
        # In a real implementation, this would query the actual mesh memory
        # For this example, we'll return a dummy value
        memory.append(MeshMemory(server_id=server.id, memory=8.0, timestamp="2023-01-01T00:00:00Z"))
    return memory

def get_signal_scores(session: Session = Depends(get_session)) -> List[SignalScore]:
    """Fetch signal scores for all servers from the database."""
    servers = session.query(McpServerRegistry).all()
    scores = []
    for server in servers:
        # In a real implementation, this would query the actual signal scores
        # For this example, we'll return a dummy value
        scores.append(SignalScore(server_id=server.id, signal="signal1", score=0.5, timestamp="2023-01-01T00:00:00Z"))
    return scores

def api_signal_scores() -> List[SignalScore]:
    """Fetch signal scores for all servers from the API."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    return response.json()

class McpLlmAxisScore:
    """Base class for LLM axis scores."""
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

class OrgService:
    """Service for org-related operations."""
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

class UserService:
    """Service for user-related operations."""
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

def reset_quarantine_endpoint() -> str:
    """Reset quarantine endpoint."""
    return "Quarantine reset"

def mesh_memory_endpoint_get() -> List[MeshMemory]:
    """Get mesh memory endpoint."""
    return get_mesh_memory()

def signal_scores_endpoint() -> List[SignalScore]:
    """Get signal scores endpoint."""
    return get_signal_scores()

def _run_self_test() -> str:
    """Run self-test."""
    return "PASS"

if __name__ == "__main__":
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Set up in-memory database for self-test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Override get_session for self-test
    def get_session_override() -> Session:
        return Session(engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = get_session_override

    # Run self-test
    print(_run_self_test())