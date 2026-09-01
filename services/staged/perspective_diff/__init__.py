from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Dict, Any
import requests
import json

app = FastAPI()

def get_mesh_memory() -> Dict[str, Any]:
    """Fetch mesh memory data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def get_mesh_scores() -> List[Dict[str, Any]]:
    """Fetch mesh scores data from ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return response.json()

def reset_server_export_api_quarantine(server_id: int, db: Session = Depends(get_session)) -> None:
    """Reset quarantine status for a server in the database."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if server:
        server.quarantined = False
        db.commit()
    else:
        raise HTTPException(status_code=404, detail="Server not found")

def dummy_post_endpoint(data: Dict[str, Any]) -> Dict[str, Any]:
    """Dummy POST endpoint for testing purposes."""
    return {"status": "success", "data": data}

def _dummy_post(data: Dict[str, Any]) -> Dict[str, Any]:
    """Internal dummy POST function for testing purposes."""
    return {"status": "success", "data": data}

def _run_self_test() -> None:
    """Self-test function to verify the module's functionality."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test get_mesh_memory
    try:
        mesh_memory = get_mesh_memory()
        assert isinstance(mesh_memory, dict)
    except Exception as e:
        print(f"get_mesh_memory test failed: {e}")
        return

    # Test get_mesh_scores
    try:
        mesh_scores = get_mesh_scores()
        assert isinstance(mesh_scores, list)
    except Exception as e:
        print(f"get_mesh_scores test failed: {e}")
        return

    # Test reset_server_export_api_quarantine
    try:
        db = TestSession()
        test_server = McpServerRegistry(id=1, name="test_server", quarantined=True)
        db.add(test_server)
        db.commit()
        reset_server_export_api_quarantine(1)
        updated_server = db.query(McpServerRegistry).filter(McpServerRegistry.id == 1).first()
        assert updated_server.quarantined is False
    except Exception as e:
        print(f"reset_server_export_api_quarantine test failed: {e}")
        return

    # Test dummy_post_endpoint
    try:
        response = dummy_post_endpoint({"key": "value"})
        assert response == {"status": "success", "data": {"key": "value"}}
    except Exception as e:
        print(f"dummy_post_endpoint test failed: {e}")
        return

    # Test _dummy_post
    try:
        response = _dummy_post({"key": "value"})
        assert response == {"status": "success", "data": {"key": "value"}}
    except Exception as e:
        print(f"_dummy_post test failed: {e}")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()