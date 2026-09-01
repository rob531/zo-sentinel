from typing import List, Dict, Any
import requests
from fastapi import Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_mesh_scores(server_id: int) -> Dict[str, Any]:
    """Fetch mesh scores for a given server ID."""
    session = Depends(get_session)
    server = session.query(McpServerRegistry).filter_by(id=server_id).first()
    if not server:
        return {"error": "Server not found"}

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
        timeout=10
    )
    if response.status_code != 200:
        return {"error": "Failed to fetch mesh scores"}

    return response.json()

def get_mesh_memory(server_id: int) -> Dict[str, Any]:
    """Fetch mesh memory for a given server ID."""
    session = Depends(get_session)
    server = session.query(McpServerRegistry).filter_by(id=server_id).first()
    if not server:
        return {"error": "Server not found"}

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
        timeout=10
    )
    if response.status_code != 200:
        return {"error": "Failed to fetch mesh memory"}

    return response.json()

def get_signal_scores(server_id: int) -> Dict[str, Any]:
    """Fetch signal scores for a given server ID."""
    session = Depends(get_session)
    server = session.query(McpServerRegistry).filter_by(id=server_id).first()
    if not server:
        return {"error": "Server not found"}

    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
        timeout=10
    )
    if response.status_code != 200:
        return {"error": "Failed to fetch signal scores"}

    return response.json()

if __name__ == "__main__":
    from app.db import get_session
    from app.models import McpServerRegistry
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test data
    session = SessionLocal()
    session.execute("CREATE TABLE McpServerRegistry (id INTEGER PRIMARY KEY, name TEXT)")
    session.execute("INSERT INTO McpServerRegistry (id, name) VALUES (1, 'Test Server')")
    session.commit()

    # Test get_mesh_scores
    scores = get_mesh_scores(1)
    if isinstance(scores, dict) and "error" not in scores:
        print("PASS")
    else:
        print("FAIL")

    # Test get_mesh_memory
    memory = get_mesh_memory(1)
    if isinstance(memory, dict) and "error" not in memory:
        print("PASS")
    else:
        print("FAIL")

    # Test get_signal_scores
    signal_scores = get_signal_scores(1)
    if isinstance(signal_scores, dict) and "error" not in signal_scores:
        print("PASS")
    else:
        print("FAIL")