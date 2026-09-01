from typing import List, Dict, Any
import requests
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, MCPSignalScores, MeshMemory

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch mesh scores for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to fetch mesh scores: {e}")

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to fetch mesh memory: {e}")

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch signal scores for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to fetch signal scores: {e}")

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
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

    # Test data
    test_server = McpServerRegistry(id=1, name="Test Server")
    test_session = SessionLocal()
    test_session.add(test_server)
    test_session.commit()

    # Test functions
    try:
        mesh_scores = get_mesh_scores(1)
        mesh_memory = get_mesh_memory(1)
        signal_scores = get_signal_scores(1)
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")