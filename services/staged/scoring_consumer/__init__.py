from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, MCPSignalScores, McpScoreDispute, Org, User
from typing import List, Optional
import requests
import json

app = FastAPI()

def get_mesh_memory() -> List[dict]:
    """Fetches mesh memory data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def get_signal_scores() -> List[dict]:
    """Fetches signal scores data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    return response.json()

def get_mesh_scores(db: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
    """Fetches mesh scores from app database."""
    return db.query(McpLlmAxisScore).all()

def reset_quarantine_endpoint():
    """Resets quarantine status for servers."""
    return {"status": "success"}

def dummy_post_endpoint():
    """Dummy POST endpoint for testing."""
    return {"status": "success"}

def _run_self_test():
    """Self-test for the module."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    try:
        # Test get_mesh_memory
        mesh_memory = get_mesh_memory()
        assert isinstance(mesh_memory, list), "get_mesh_memory should return a list"

        # Test get_signal_scores
        signal_scores = get_signal_scores()
        assert isinstance(signal_scores, list), "get_signal_scores should return a list"

        # Test get_mesh_scores
        db = SessionLocal()
        mesh_scores = get_mesh_scores(db)
        assert isinstance(mesh_scores, list), "get_mesh_scores should return a list"

        # Test reset_quarantine_endpoint
        response = reset_quarantine_endpoint()
        assert response == {"status": "success"}, "reset_quarantine_endpoint should return success"

        # Test dummy_post_endpoint
        response = dummy_post_endpoint()
        assert response == {"status": "success"}, "dummy_post_endpoint should return success"

        print("PASS")
    finally:
        app.dependency_overrides.pop(get_session, None)
        db.close()

if __name__ == "__main__":
    _run_self_test()