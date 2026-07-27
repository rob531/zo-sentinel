import requests
from typing import Any, Dict, List, Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, Org, User

MESH_STORE_URL = "http://127.0.0.1:8772/query"

def query_mesh_store(payload: Dict[str, Any]) -> Any:
    """Internal helper to query the ZoComputer store."""
    try:
        response = requests.post(MESH_STORE_URL, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None

def get_mesh_memory(query: str) -> Optional[Any]:
    """Retrieves data from the mesh_memory table via the ZoComputer store."""
    return query_mesh_store({"query": f"SELECT * FROM mesh_memory WHERE {query}"})

def get_signal_scores(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Retrieves signal scores from the mcp_signal_scores table."""
    # Construct a simple query based on params
    where_clause = " AND ".join([f"{k} = '{v}'" for k, v in params.items()])
    query = f"SELECT * FROM mcp_signal_scores WHERE {where_clause}" if where_clause else "SELECT * FROM mcp_signal_scores"
    result = query_mesh_store({"query": query})
    return result if isinstance(result, list) else []

def get_mesh_scores(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Retrieves mesh scores from the mcp_signal_scores table."""
    return get_signal_scores(params)

def update_quarantine_status(db: Session, server_id: str, status: bool):
    """Updates the quarantine status of a server in the app database."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if server:
        server.quarantined = status
        db.commit()
        db.refresh(server)
    return server

if __name__ == "__main__":
    import unittest
    from unittest.mock import MagicMock, patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup mock DB for self-test
    engine = create_engine("sqlite:///:memory:")
    # We don't need full models for the logic test, just the session behavior
    SessionLocal = sessionmaker(bind=engine)
    mock_session = SessionLocal()

    with patch("requests.post") as mock_post:
        # Mock Mesh Store Response
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = [{"score": 0.95, "signal": "test_signal"}]

        # Test Mesh Memory
        mem = get_mesh_memory("key = 'test'")
        assert mem is not None
        
        # Test Signal Scores
        scores = get_signal_scores({"signal": "test"})
        assert len(scores) > 0
        
        # Test Mesh Scores
        m_scores = get_mesh_scores({"signal": "test"})
        assert len(m_scores) > 0

        # Test Quarantine Update (Mocking the model query)
        with patch("app.models.McpServerRegistry") as mock_model:
            mock_server = MagicMock()
            mock_server.id = "srv_123"
            mock_model.query.return_value.filter.return_value.first.return_value = mock_server
            
            res = update_quarantine_status(mock_session, "srv_123", True)
            assert res.quarantined is True

    print("PASS")