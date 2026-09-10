from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Fetch mesh scores from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory() -> List[Dict[str, Any]]:
    """Fetch mesh memory from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory"},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch signal scores from the app database."""
    try:
        scores = db.query(McpLlmAxisScore).all()
        return [{"id": score.id, "score": score.score} for score in scores]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Fetch mesh scores from the ZoComputer store."""
    return get_mesh_scores_endpoint()

def mesh_memory_endpoint() -> List[Dict[str, Any]]:
    """Fetch mesh memory from the ZoComputer store."""
    return get_mesh_memory()

def reset_server_export_quarantine_api() -> Dict[str, Any]:
    """Reset server export quarantine."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/reset_quarantine",
            timeout=5
        )
        response.raise_for_status()
        return {"status": "success"}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def reset_quarantine_endpoint() -> Dict[str, Any]:
    """Reset quarantine."""
    return reset_server_export_quarantine_api()

def _run_self_test():
    """Self-test for the service."""
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Test get_mesh_scores_endpoint
    try:
        scores = get_mesh_scores_endpoint()
        assert isinstance(scores, list)
    except Exception as e:
        print(f"get_mesh_scores_endpoint test failed: {e}")
        return

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory()
        assert isinstance(memory, list)
    except Exception as e:
        print(f"get_mesh_memory test failed: {e}")
        return

    # Test get_signal_scores
    try:
        scores = get_signal_scores()
        assert isinstance(scores, list)
    except Exception as e:
        print(f"get_signal_scores test failed: {e}")
        return

    # Test mesh_scores_endpoint
    try:
        scores = mesh_scores_endpoint()
        assert isinstance(scores, list)
    except Exception as e:
        print(f"mesh_scores_endpoint test failed: {e}")
        return

    # Test mesh_memory_endpoint
    try:
        memory = mesh_memory_endpoint()
        assert isinstance(memory, list)
    except Exception as e:
        print(f"mesh_memory_endpoint test failed: {e}")
        return

    # Test reset_server_export_quarantine_api
    try:
        result = reset_server_export_quarantine_api()
        assert isinstance(result, dict)
    except Exception as e:
        print(f"reset_server_export_quarantine_api test failed: {e}")
        return

    # Test reset_quarantine_endpoint
    try:
        result = reset_quarantine_endpoint()
        assert isinstance(result, dict)
    except Exception as e:
        print(f"reset_quarantine_endpoint test failed: {e}")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()