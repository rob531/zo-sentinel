from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Optional
import json

app = FastAPI()

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[dict]:
    """Fetch signal scores for a given server_id from the database."""
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        return None

    scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    if not scores:
        return None

    return {
        "server_id": server_id,
        "scores": [{"axis": score.axis, "value": score.value} for score in scores]
    }

def get_mesh_scores(server_id: int) -> Optional[dict]:
    """Fetch mesh scores for a given server_id from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return {
            "server_id": server_id,
            "scores": data
        }
    except requests.exceptions.RequestException:
        return None

def get_mesh_memory(server_id: int) -> Optional[dict]:
    """Fetch mesh memory for a given server_id from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return {
            "server_id": server_id,
            "memory": data
        }
    except requests.exceptions.RequestException:
        return None

def reset_server_export_api_quarantine(server_id: int, session: Session = Depends(get_session)) -> bool:
    """Reset the export API quarantine status for a given server_id."""
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        return False

    server.export_api_quarantine = False
    session.commit()
    return True

def _run_self_test():
    """Self-test for the service."""
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test get_signal_scores
    session = TestSession()
    test_server = McpServerRegistry(id=1, export_api_quarantine=False)
    test_score = McpLlmAxisScore(server_id=1, axis="test_axis", value=0.5)
    session.add(test_server)
    session.add(test_score)
    session.commit()

    result = get_signal_scores(1)
    if result is None or result["server_id"] != 1 or len(result["scores"]) != 1:
        print("FAIL: get_signal_scores")
        return

    # Test get_mesh_scores
    # Mock the response from ZoComputer store
    def mock_requests_post(url, json, timeout):
        if json["query"] == "SELECT * FROM mcp_signal_scores WHERE server_id = 1":
            return type('Response', (), {
                'json': lambda: [{"axis": "test_axis", "value": 0.5}],
                'raise_for_status': lambda: None
            })()
        return type('Response', (), {
            'json': lambda: [],
            'raise_for_status': lambda: None
        })()

    app.dependency_overrides[requests.post] = mock_requests_post
    result = get_mesh_scores(1)
    if result is None or result["server_id"] != 1 or len(result["scores"]) != 1:
        print("FAIL: get_mesh_scores")
        return

    # Test get_mesh_memory
    result = get_mesh_memory(1)
    if result is None or result["server_id"] != 1:
        print("FAIL: get_mesh_memory")
        return

    # Test reset_server_export_api_quarantine
    result = reset_server_export_api_quarantine(1)
    if not result:
        print("FAIL: reset_server_export_api_quarantine")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()