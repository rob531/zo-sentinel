from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve mesh scores for a given server ID."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve signal scores for a given server ID."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Retrieve mesh memory for a given server ID."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def reset_server_export_api_quarantine(server_id: int, session: Session = Depends(get_session)) -> None:
    """Reset server export API quarantine status."""
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if server:
        server.quarantine_status = False
        session.commit()
    else:
        raise HTTPException(status_code=404, detail="Server not found")

def _run_self_test() -> None:
    """Self-test for the module."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test get_mesh_scores
    try:
        scores = get_mesh_scores(1)
        assert isinstance(scores, dict), "get_mesh_scores did not return a dictionary"
    except Exception as e:
        print(f"get_mesh_scores test failed: {e}")
        return

    # Test get_signal_scores
    try:
        scores = get_signal_scores(1)
        assert isinstance(scores, dict), "get_signal_scores did not return a dictionary"
    except Exception as e:
        print(f"get_signal_scores test failed: {e}")
        return

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory(1)
        assert isinstance(memory, dict), "get_mesh_memory did not return a dictionary"
    except Exception as e:
        print(f"get_mesh_memory test failed: {e}")
        return

    # Test reset_server_export_api_quarantine
    try:
        reset_server_export_api_quarantine(1)
    except Exception as e:
        print(f"reset_server_export_api_quarantine test failed: {e}")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()