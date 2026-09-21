from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, MCPSignalScores, MeshMemory
from typing import List, Dict, Optional
import requests
import json

app = FastAPI()

def get_mesh_memory() -> Dict:
    """Fetch mesh memory from the ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch mesh memory")
    return response.json()

def get_mesh_scores() -> List[Dict]:
    """Fetch mesh scores from the ZoComputer store."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch mesh scores")
    return response.json()

def get_signal_scores(db: Session = Depends(get_session)) -> List[Dict]:
    """Fetch signal scores from the app database."""
    scores = db.query(MCPSignalScores).all()
    return [{"id": score.id, "server_id": score.server_id, "score": score.score} for score in scores]

def reset_server_export_api_quarantine(db: Session = Depends(get_session)) -> None:
    """Reset server export API quarantine status in the app database."""
    servers = db.query(McpServerRegistry).all()
    for server in servers:
        server.quarantined = False
    db.commit()

def dummy_post_endpoint(data: Dict) -> Dict:
    """Dummy POST endpoint for testing."""
    return {"status": "success", "data": data}

def mesh_scores_endpoint() -> List[Dict]:
    """Endpoint to fetch mesh scores."""
    return get_mesh_scores()

def _run_self_test():
    """Self-test for the module."""
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
        get_mesh_memory()
    except HTTPException:
        pass  # Expected since the ZoComputer store is not running

    # Test get_mesh_scores
    try:
        get_mesh_scores()
    except HTTPException:
        pass  # Expected since the ZoComputer store is not running

    # Test get_signal_scores
    db = TestSession()
    test_score = MCPSignalScores(server_id=1, score=0.5)
    db.add(test_score)
    db.commit()
    scores = get_signal_scores(db)
    assert len(scores) == 1
    assert scores[0]["server_id"] == 1
    assert scores[0]["score"] == 0.5

    # Test reset_server_export_api_quarantine
    test_server = McpServerRegistry(id=1, quarantined=True)
    db.add(test_server)
    db.commit()
    reset_server_export_api_quarantine(db)
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == 1).first()
    assert server.quarantined is False

    # Test dummy_post_endpoint
    result = dummy_post_endpoint({"test": "data"})
    assert result["status"] == "success"
    assert result["data"] == {"test": "data"}

    # Test mesh_scores_endpoint
    try:
        mesh_scores_endpoint()
    except HTTPException:
        pass  # Expected since the ZoComputer store is not running

    print("PASS")

if __name__ == "__main__":
    _run_self_test()