from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpLlmAxisScore, McpServerRegistry, Org, User
from typing import List, Optional
import requests

def get_mesh_scores() -> List[dict]:
    """Get mesh scores from the write service."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return response.json()

def get_mesh_memory_by_id(mesh_memory_id: int) -> dict:
    """Get mesh memory by ID from the write service."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory WHERE id = :id",
        "params": {"id": mesh_memory_id}
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def get_mesh_memory_endpoint() -> List[dict]:
    """Get all mesh memory from the write service."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def get_score_disputes_endpoint(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
    """Get all score disputes from the app database."""
    return db.query(McpScoreDispute).all()

def get_users(db: Session = Depends(get_session)) -> List[User]:
    """Get all users from the app database."""
    return db.query(User).all()

def dummy_post_api() -> str:
    """Dummy POST API endpoint."""
    return "Dummy POST API response"

class McpLlmAxisScoreService:
    """Service for McpLlmAxisScore operations."""

    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_scores(self) -> List[McpLlmAxisScore]:
        """Get all LLM axis scores from the app database."""
        return self.db.query(McpLlmAxisScore).all()

class OrgService:
    """Service for Org operations."""

    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_orgs(self) -> List[Org]:
        """Get all orgs from the app database."""
        return self.db.query(Org).all()

class UserService:
    """Service for User operations."""

    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_users(self) -> List[User]:
        """Get all users from the app database."""
        return self.db.query(User).all()

def mesh_scores_endpoint() -> List[dict]:
    """Get mesh scores from the write service."""
    return get_mesh_scores()

def mesh_memory_endpoint() -> List[dict]:
    """Get all mesh memory from the write service."""
    return get_mesh_memory_endpoint()

def get_mesh_memory_endpoint() -> List[dict]:
    """Get all mesh memory from the write service."""
    return get_mesh_memory_endpoint()

def get_score_disputes_endpoint() -> List[McpScoreDispute]:
    """Get all score disputes from the app database."""
    return get_score_disputes_endpoint()

def get_users() -> List[User]:
    """Get all users from the app database."""
    return get_users()

def dummy_post_api() -> str:
    """Dummy POST API endpoint."""
    return dummy_post_api()

def signal_scores_endpoint() -> List[dict]:
    """Get signal scores from the write service."""
    return get_mesh_scores()

def mesh_scores() -> List[dict]:
    """Get mesh scores from the write service."""
    return get_mesh_scores()

def test() -> str:
    """Test function."""
    return "Test passed"

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    # Create a test engine and session
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)
    from app.db import get_session as original_get_session
    from app.db import SessionLocal

    # Override the get_session dependency for testing
    def get_test_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.dependency_overrides[original_get_session] = get_test_session

    # Test the endpoints
    client = TestClient(app)

    # Test get_mesh_scores
    response = client.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    assert response.status_code == 200

    # Test get_mesh_memory_by_id
    response = client.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory WHERE id = :id",
        "params": {"id": 1}
    })
    assert response.status_code == 200

    # Test get_mesh_memory_endpoint
    response = client.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    assert response.status_code == 200

    # Test get_score_disputes_endpoint
    response = client.get("/score_disputes")
    assert response.status_code == 200

    # Test get_users
    response = client.get("/users")
    assert response.status_code == 200

    # Test dummy_post_api
    response = client.post("/dummy")
    assert response.status_code == 200

    print("PASS")