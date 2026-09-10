from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Optional[dict]:
    """Fetch mesh memory for a given server ID from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json().get("data", [{}])[0]
    except requests.exceptions.RequestException:
        return None

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[List[dict]]:
    """Fetch signal scores for a given server ID from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json().get("data", [])
    except requests.exceptions.RequestException:
        return None

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[List[dict]]:
    """Fetch mesh scores for a given server ID from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM McpLlmAxisScore WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json().get("data", [])
    except requests.exceptions.RequestException:
        return None

def dummy_post_endpoint(data: dict, session: Session = Depends(get_session)) -> dict:
    """Dummy POST endpoint for testing purposes."""
    return {"status": "success", "data": data}

def _run_self_test() -> None:
    """Self-test for the module."""
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables
    McpServerRegistry.metadata.create_all(engine)
    McpLlmAxisScore.metadata.create_all(engine)
    McpScoreDispute.metadata.create_all(engine)
    Org.metadata.create_all(engine)
    User.metadata.create_all(engine)

    # Test get_mesh_memory
    test_server = McpServerRegistry(server_id=1, name="test_server")
    session = SessionLocal()
    session.add(test_server)
    session.commit()
    assert get_mesh_memory(1) is not None

    # Test get_signal_scores
    assert get_signal_scores(1) is not None

    # Test get_mesh_scores
    assert get_mesh_scores(1) is not None

    # Test dummy_post_endpoint
    assert dummy_post_endpoint({"test": "data"}) == {"status": "success", "data": {"test": "data"}}

    print("PASS")

if __name__ == "__main__":
    _run_self_test()