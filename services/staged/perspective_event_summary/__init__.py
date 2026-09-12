from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional
import json

router = APIRouter()

def get_mesh_scores(server_ids: List[int]) -> Dict[int, Dict[str, float]]:
    """Fetch mesh scores for given server IDs from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN ({','.join(map(str, server_ids))})"}
    )
    if response.status_code != 200:
        return {}
    data = response.json()
    return {row['server_id']: row['scores'] for row in data}

def get_mesh_memory(server_ids: List[int]) -> Dict[int, Dict[str, float]]:
    """Fetch mesh memory for given server IDs from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id IN ({','.join(map(str, server_ids))})"}
    )
    if response.status_code != 200:
        return {}
    data = response.json()
    return {row['server_id']: row['memory'] for row in data}

def get_signal_scores(server_ids: List[int], db: Session = Depends(get_session)) -> Dict[int, Dict[str, float]]:
    """Fetch signal scores for given server IDs from MCP tables."""
    servers = db.query(McpServerRegistry).filter(McpServerRegistry.id.in_(server_ids)).all()
    scores = {}
    for server in servers:
        axis_scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server.id).first()
        if axis_scores:
            scores[server.id] = axis_scores.scores
    return scores

def _dummy_post() -> str:
    """Dummy POST endpoint for testing."""
    return "dummy_post"

def _post_query(query: str) -> Optional[List[Dict]]:
    """Post a query to ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query}
    )
    if response.status_code == 200:
        return response.json()
    return None

def _run_self_test() -> str:
    """Self-test for the module."""
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore

    # Override the session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    # Insert test data
    db = SessionLocal()
    server = McpServerRegistry(id=1, name="test_server")
    db.add(server)
    db.commit()

    axis_scores = McpLlmAxisScore(server_id=1, scores={"test": 0.5})
    db.add(axis_scores)
    db.commit()

    # Test get_signal_scores
    scores = get_signal_scores([1], db)
    if scores != {1: {"test": 0.5}}:
        return "FAIL"

    # Test get_mesh_scores
    mesh_scores = get_mesh_scores([1])
    if mesh_scores != {1: {"test": 0.5}}:
        return "FAIL"

    # Test get_mesh_memory
    mesh_memory = get_mesh_memory([1])
    if mesh_memory != {1: {"test": 0.5}}:
        return "FAIL"

    return "PASS"

if __name__ == "__main__":
    print(_run_self_test())