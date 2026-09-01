from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional

app = FastAPI()

def get_mesh_scores(server_ids: List[int]) -> Dict[int, Dict[str, float]]:
    """Fetch mesh scores for given server IDs from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mcp_signal_scores WHERE server_id IN :server_ids",
              "params": {"server_ids": server_ids}}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return {row["server_id"]: row["scores"] for row in response.json()}

def get_mesh_memory(server_ids: List[int]) -> Dict[int, Dict[str, str]]:
    """Fetch mesh memory for given server IDs from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mesh_memory WHERE server_id IN :server_ids",
              "params": {"server_ids": server_ids}}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return {row["server_id"]: row["memory"] for row in response.json()}

def get_signal_scores(server_ids: List[int], db: Session = Depends(get_session)) -> Dict[int, Dict[str, float]]:
    """Fetch signal scores for given server IDs from app database."""
    scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id.in_(server_ids)).all()
    return {score.server_id: score.scores for score in scores}

def reset_server_export_api_quarantine(server_id: int, db: Session = Depends(get_session)) -> None:
    """Reset export API quarantine flag for a server."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if server:
        server.export_api_quarantine = False
        db.commit()
    else:
        raise HTTPException(status_code=404, detail="Server not found")

if __name__ == "__main__":
    import pytest
    from app.db import get_test_session
    from app.models import Base

    # Override the dependency for self-test
    app.dependency_overrides[get_session] = get_test_session

    # Create test data
    test_server = McpServerRegistry(id=1, name="test-server", export_api_quarantine=True)
    test_score = McpLlmAxisScore(server_id=1, scores={"score1": 0.5, "score2": 0.7})

    # Test get_signal_scores
    assert get_signal_scores([1]) == {1: {"score1": 0.5, "score2": 0.7}}

    # Test reset_server_export_api_quarantine
    reset_server_export_api_quarantine(1)
    assert not test_server.export_api_quarantine

    print("PASS")