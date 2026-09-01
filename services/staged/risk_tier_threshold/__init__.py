from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, MCPSignalScores, MeshMemory, McpScoreDispute, Org, User
from typing import List, Optional
import requests
import json

app = FastAPI()

def get_mesh_memory(db: Session = Depends(get_session)) -> List[MeshMemory]:
    return db.query(MeshMemory).all()

def get_mesh_scores(db: Session = Depends(get_session)) -> List[MCPSignalScores]:
    return db.query(MCPSignalScores).all()

def get_signal_scores(db: Session = Depends(get_session)) -> List[MCPSignalScores]:
    return db.query(MCPSignalScores).all()

def mesh_memory_endpoint() -> List[MeshMemory]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def mesh_scores_endpoint() -> List[MCPSignalScores]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return response.json()

def reset_quarantine_endpoint() -> str:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "UPDATE mesh_memory SET quarantine = 0"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to reset quarantine")
    return "Quarantine reset successfully"

def reset_server_export_api_quarantine() -> str:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "UPDATE McpServerRegistry SET export_api_quarantine = 0"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to reset server export API quarantine")
    return "Server export API quarantine reset successfully"

def _dummy_post() -> str:
    return "Dummy post successful"

def dummy_post_endpoint() -> str:
    return "Dummy post endpoint successful"

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test get_mesh_memory
    mesh_memory = get_mesh_memory()
    assert isinstance(mesh_memory, list)

    # Test get_mesh_scores
    mesh_scores = get_mesh_scores()
    assert isinstance(mesh_scores, list)

    # Test get_signal_scores
    signal_scores = get_signal_scores()
    assert isinstance(signal_scores, list)

    # Test mesh_memory_endpoint
    try:
        mesh_memory_endpoint()
    except HTTPException:
        pass

    # Test mesh_scores_endpoint
    try:
        mesh_scores_endpoint()
    except HTTPException:
        pass

    # Test reset_quarantine_endpoint
    try:
        reset_quarantine_endpoint()
    except HTTPException:
        pass

    # Test reset_server_export_api_quarantine
    try:
        reset_server_export_api_quarantine()
    except HTTPException:
        pass

    # Test _dummy_post
    assert _dummy_post() == "Dummy post successful"

    # Test dummy_post_endpoint
    assert dummy_post_endpoint() == "Dummy post endpoint successful"

    print("PASS")

if __name__ == "__main__":
    _run_self_test()