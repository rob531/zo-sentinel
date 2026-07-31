from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import User
from typing import List, Optional
import requests

app = FastAPI()

def get_mesh_memory() -> List[dict]:
    """Fetch mesh memory data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def get_signal_scores() -> List[dict]:
    """Fetch signal scores data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    return response.json()

def mesh_memory_endpoint() -> List[dict]:
    """Endpoint to get mesh memory data."""
    return get_mesh_memory()

def mesh_scores_endpoint() -> List[dict]:
    """Endpoint to get mesh scores data."""
    return get_signal_scores()

def get_mesh_memory_endpoint() -> List[dict]:
    """Endpoint to get mesh memory data."""
    return get_mesh_memory()

def get_signal_scores_endpoint() -> List[dict]:
    """Endpoint to get signal scores data."""
    return get_signal_scores()

def reset_quarantine_api() -> dict:
    """Endpoint to reset quarantine status."""
    return {"status": "success"}

def _dummy_post() -> dict:
    """Dummy POST endpoint for testing."""
    return {"status": "success"}

def _run_self_test(db: Session = Depends(get_session)) -> str:
    """Self-test for the module."""
    try:
        # Test User model
        test_user = User(email="test@example.com")
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

        # Test mesh memory endpoint
        mesh_memory = get_mesh_memory()
        if not isinstance(mesh_memory, list):
            return "FAIL: mesh_memory is not a list"

        # Test signal scores endpoint
        signal_scores = get_signal_scores()
        if not isinstance(signal_scores, list):
            return "FAIL: signal_scores is not a list"

        return "PASS"
    except Exception as e:
        return f"FAIL: {str(e)}"
    finally:
        db.rollback()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print(_run_self_test())