from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional
import json

app = FastAPI()

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Fetch signal scores for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    return response.json()

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Fetch mesh scores for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return response.json()

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def dummy_post_endpoint(data: Dict, session: Session = Depends(get_session)) -> Dict:
    """Dummy endpoint for testing purposes."""
    return {"status": "success", "data": data}

def reset_server_export_api_quarantine(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Reset server export API quarantine status."""
    server = session.query(McpServerRegistry).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server.export_api_quarantine = False
    session.commit()
    return {"status": "success", "server_id": server_id}

def reset_server_export_api_quarantine_endpoint(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Endpoint to reset server export API quarantine status."""
    return reset_server_export_api_quarantine(server_id, session)

def _run_self_test() -> None:
    """Self-test for the module."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test dummy_post_endpoint
    test_data = {"key": "value"}
    result = dummy_post_endpoint(test_data)
    assert result == {"status": "success", "data": test_data}

    # Test reset_server_export_api_quarantine_endpoint
    test_server_id = 1
    result = reset_server_export_api_quarantine_endpoint(test_server_id)
    assert result == {"status": "success", "server_id": test_server_id}

    print("PASS")

if __name__ == "__main__":
    _run_self_test()