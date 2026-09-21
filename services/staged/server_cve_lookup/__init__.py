from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> List[Dict]:
    """Fetch signal scores for a given server."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
        timeout=5
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")

    return response.json()

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> List[Dict]:
    """Fetch mesh memory for a given server."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
        timeout=5
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")

    return response.json()

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> List[Dict]:
    """Fetch mesh scores for a given server."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM McpLlmAxisScore WHERE server_id = {server_id}"},
        timeout=5
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")

    return response.json()

def reset_server_export_api_quarantine(server_id: int, db: Session = Depends(get_session)) -> Dict:
    """Reset the export API quarantine for a given server."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server.export_api_quarantine = False
    db.commit()
    return {"status": "success", "server_id": server_id}

def _dummy_post(server_id: int, db: Session = Depends(get_session)) -> Dict:
    """Dummy post endpoint for testing."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    return {"status": "success", "server_id": server_id}

def _post_query(query: str, db: Session = Depends(get_session)) -> List[Dict]:
    """Post a query to the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query},
        timeout=5
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to execute query")

    return response.json()

def _run_self_test() -> None:
    """Self-test for the module."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Test get_signal_scores
    try:
        scores = get_signal_scores(1)
        assert isinstance(scores, list), "get_signal_scores did not return a list"
    except Exception as e:
        print(f"get_signal_scores test failed: {e}")
        return

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory(1)
        assert isinstance(memory, list), "get_mesh_memory did not return a list"
    except Exception as e:
        print(f"get_mesh_memory test failed: {e}")
        return

    # Test get_mesh_scores
    try:
        mesh_scores = get_mesh_scores(1)
        assert isinstance(mesh_scores, list), "get_mesh_scores did not return a list"
    except Exception as e:
        print(f"get_mesh_scores test failed: {e}")
        return

    # Test reset_server_export_api_quarantine
    try:
        result = reset_server_export_api_quarantine(1)
        assert result["status"] == "success", "reset_server_export_api_quarantine did not return success"
    except Exception as e:
        print(f"reset_server_export_api_quarantine test failed: {e}")
        return

    # Test _dummy_post
    try:
        result = _dummy_post(1)
        assert result["status"] == "success", "_dummy_post did not return success"
    except Exception as e:
        print(f"_dummy_post test failed: {e}")
        return

    # Test _post_query
    try:
        result = _post_query("SELECT 1")
        assert isinstance(result, list), "_post_query did not return a list"
    except Exception as e:
        print(f"_post_query test failed: {e}")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()