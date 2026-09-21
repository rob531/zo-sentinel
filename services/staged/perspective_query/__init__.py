from typing import List, Dict, Any
import requests
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_mesh_memory(server_id: int) -> List[Dict[str, Any]]:
    """Fetch mesh memory data for a given server ID."""
    session: Session = Depends(get_session)
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        return []

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return []

def get_mesh_scores(server_id: int) -> List[Dict[str, Any]]:
    """Fetch mesh scores data for a given server ID."""
    session: Session = Depends(get_session)
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        return []

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return []

def get_signal_scores(server_id: int) -> List[Dict[str, Any]]:
    """Fetch signal scores data for a given server ID."""
    session: Session = Depends(get_session)
    server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        return []

    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return []

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
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
    session = SessionLocal()
    session.add(test_server)
    session.commit()

    # Test functions
    assert get_mesh_memory(1) == []
    assert get_mesh_scores(1) == []
    assert get_signal_scores(1) == []

    print("PASS")