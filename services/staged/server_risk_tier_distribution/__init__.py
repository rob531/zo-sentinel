from typing import List, Dict, Any
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, MCPSignalScores, MeshMemory, Org, User
import requests

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch mesh memory data for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    return response.json()

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch mesh scores data for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    return response.json()

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch signal scores data for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    return response.json()

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    # Test get_mesh_memory
    test_server_id = 1
    mesh_memory = get_mesh_memory(test_server_id)
    print(f"get_mesh_memory test: {'PASS' if isinstance(mesh_memory, list) else 'FAIL'}")

    # Test get_mesh_scores
    mesh_scores = get_mesh_scores(test_server_id)
    print(f"get_mesh_scores test: {'PASS' if isinstance(mesh_scores, list) else 'FAIL'}")

    # Test get_signal_scores
    signal_scores = get_signal_scores(test_server_id)
    print(f"get_signal_scores test: {'PASS' if isinstance(signal_scores, list) else 'FAIL'}")

    test_session.close()