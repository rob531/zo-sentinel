from typing import List, Dict, Any
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from pydantic import BaseModel

class SignalScore(BaseModel):
    server_id: int
    signal_name: str
    score: float
    timestamp: str

class MeshScore(BaseModel):
    server_id: int
    score: float
    timestamp: str

class MeshMemory(BaseModel):
    server_id: int
    memory: Dict[str, Any]
    timestamp: str

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> List[SignalScore]:
    """Fetch signal scores for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    return [SignalScore(**row) for row in response.json()]

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> List[MeshScore]:
    """Fetch mesh scores for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return [MeshScore(**row) for row in response.json()]

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> List[MeshMemory]:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"
        }
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return [MeshMemory(**row) for row in response.json()]

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Test data
    test_server_id = 1

    # Test get_signal_scores
    try:
        scores = get_signal_scores(test_server_id)
        print(f"get_signal_scores test: {'PASS' if scores else 'FAIL'}")
    except Exception as e:
        print(f"get_signal_scores test: FAIL - {str(e)}")

    # Test get_mesh_scores
    try:
        scores = get_mesh_scores(test_server_id)
        print(f"get_mesh_scores test: {'PASS' if scores else 'FAIL'}")
    except Exception as e:
        print(f"get_mesh_scores test: FAIL - {str(e)}")

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory(test_server_id)
        print(f"get_mesh_memory test: {'PASS' if memory else 'FAIL'}")
    except Exception as e:
        print(f"get_mesh_memory test: FAIL - {str(e)}")

    # Cleanup
    app.dependency_overrides.clear()