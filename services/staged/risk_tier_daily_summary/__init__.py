from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests
import json

app = FastAPI()

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[List[dict]]:
    """Fetch mesh scores for a given server from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code == 200:
        return response.json()
    return None

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Optional[List[dict]]:
    """Fetch mesh memory for a given server from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code == 200:
        return response.json()
    return None

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[List[dict]]:
    """Fetch signal scores for a given server from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code == 200:
        return response.json()
    return None

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Mock data for self-test
    mock_server = McpServerRegistry(id=1, name="test_server", org_id=1)
    SessionLocal().add(mock_server)
    SessionLocal().commit()

    # Test functions
    mesh_scores = get_mesh_scores(1)
    mesh_memory = get_mesh_memory(1)
    signal_scores = get_signal_scores(1)

    if mesh_scores is not None and mesh_memory is not None and signal_scores is not None:
        print("PASS")
    else:
        print("FAIL")