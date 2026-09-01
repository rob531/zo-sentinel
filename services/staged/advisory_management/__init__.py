from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from typing import List, Dict, Any
import requests
import json

router = APIRouter()

def get_mesh_memory() -> Dict[str, Any]:
    """Retrieve mesh memory data from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def mesh_memory_endpoint() -> Dict[str, Any]:
    """Endpoint to retrieve mesh memory data."""
    return get_mesh_memory()

def mesh_scores_endpoint() -> Dict[str, Any]:
    """Endpoint to retrieve mesh scores data."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def signal_scores_endpoint() -> Dict[str, Any]:
    """Endpoint to retrieve signal scores data."""
    return mesh_scores_endpoint()

def reset_quarantine_endpoint() -> Dict[str, Any]:
    """Endpoint to reset quarantine status."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "UPDATE McpServerRegistry SET quarantine = false"},
            timeout=10
        )
        response.raise_for_status()
        return {"status": "success"}
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def _run_self_test(db: Session = Depends(get_session)) -> None:
    """Self-test for the service."""
    try:
        # Test mesh_memory_endpoint
        mesh_memory = mesh_memory_endpoint()
        assert isinstance(mesh_memory, dict), "mesh_memory_endpoint failed"

        # Test mesh_scores_endpoint
        mesh_scores = mesh_scores_endpoint()
        assert isinstance(mesh_scores, dict), "mesh_scores_endpoint failed"

        # Test signal_scores_endpoint
        signal_scores = signal_scores_endpoint()
        assert isinstance(signal_scores, dict), "signal_scores_endpoint failed"

        # Test reset_quarantine_endpoint
        reset_result = reset_quarantine_endpoint()
        assert reset_result == {"status": "success"}, "reset_quarantine_endpoint failed"

        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")

if __name__ == "__main__":
    from app.db import get_session
    from app.models import McpServerRegistry
    from fastapi import Depends
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables for self-test
    McpServerRegistry.__table__.create(engine)

    _run_self_test()