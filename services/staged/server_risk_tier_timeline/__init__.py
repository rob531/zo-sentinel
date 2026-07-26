from typing import List, Dict, Any
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

def get_mesh_memory() -> List[Dict[str, Any]]:
    """Fetch mesh memory data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    response.raise_for_status()
    return response.json()

def get_signal_scores() -> List[Dict[str, Any]]:
    """Fetch signal scores data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    response.raise_for_status()
    return response.json()

def get_mesh_scores(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch mesh scores from app database."""
    results = db.query(McpLlmAxisScore).all()
    return [{"id": r.id, "score": r.score, "axis": r.axis} for r in results]

def get_server_registry(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch server registry from app database."""
    results = db.query(McpServerRegistry).all()
    return [{"id": r.id, "name": r.name, "status": r.status} for r in results]

def get_score_disputes(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch score disputes from app database."""
    results = db.query(McpScoreDispute).all()
    return [{"id": r.id, "score_id": r.score_id, "reason": r.reason} for r in results]

def get_orgs(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch organizations from app database."""
    results = db.query(Org).all()
    return [{"id": r.id, "name": r.name} for r in results]

def get_users(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch users from app database."""
    results = db.query(User).all()
    return [{"id": r.id, "name": r.name, "email": r.email} for r in results]

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Test setup
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    # Override dependencies for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: test_session

    # Test functions
    try:
        # Test mesh memory
        mesh_memory = get_mesh_memory()
        assert isinstance(mesh_memory, list)

        # Test signal scores
        signal_scores = get_signal_scores()
        assert isinstance(signal_scores, list)

        # Test mesh scores
        mesh_scores = get_mesh_scores()
        assert isinstance(mesh_scores, list)

        # Test server registry
        server_registry = get_server_registry()
        assert isinstance(server_registry, list)

        # Test score disputes
        score_disputes = get_score_disputes()
        assert isinstance(score_disputes, list)

        # Test orgs
        orgs = get_orgs()
        assert isinstance(orgs, list)

        # Test users
        users = get_users()
        assert isinstance(users, list)

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        test_session.close()