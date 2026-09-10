from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
import json

router = APIRouter()

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Fetch mesh scores for a given server from the ZoComputer store."""
    query = f"SELECT * FROM mesh_scores WHERE server_id = {server_id}"
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh scores")
    return response.json()

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    query = f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    return response.json()

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Fetch signal scores for a given server from the ZoComputer store."""
    query = f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    return response.json()

def reset_server_export_api_quarantine_endpoint(server_id: int, db: Session = Depends(get_session)) -> None:
    """Reset the export API quarantine endpoint for a given server."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if server:
        server.export_api_quarantine = False
        db.commit()
    else:
        raise HTTPException(status_code=404, detail="Server not found")

if __name__ == "__main__":
    from app.db import SessionLocal
    from app import dependency_overrides

    # Override the database session for self-test
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Self-test
    try:
        # Test get_mesh_scores
        mesh_scores = get_mesh_scores(1)
        assert isinstance(mesh_scores, dict), "get_mesh_scores did not return a dictionary"

        # Test get_mesh_memory
        mesh_memory = get_mesh_memory(1)
        assert isinstance(mesh_memory, dict), "get_mesh_memory did not return a dictionary"

        # Test get_signal_scores
        signal_scores = get_signal_scores(1)
        assert isinstance(signal_scores, dict), "get_signal_scores did not return a dictionary"

        # Test reset_server_export_api_quarantine_endpoint
        reset_server_export_api_quarantine_endpoint(1)

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")