from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from pydantic import BaseModel

router = APIRouter()

class SignalScore(BaseModel):
    server_id: int
    signal_score: float
    timestamp: str

class MeshScore(BaseModel):
    server_id: int
    mesh_score: float
    timestamp: str

class MeshMemory(BaseModel):
    server_id: int
    memory: float
    timestamp: str

def get_signal_scores(server_ids: List[int], db: Session = Depends(get_session)) -> List[SignalScore]:
    """Fetch signal scores for given server IDs from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT server_id, signal_score, timestamp FROM mcp_signal_scores WHERE server_id IN ({})".format(
                    ",".join(map(str, server_ids))
                )
            }
        )
        response.raise_for_status()
        return [SignalScore(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_scores(server_ids: List[int], db: Session = Depends(get_session)) -> List[MeshScore]:
    """Fetch mesh scores for given server IDs from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT server_id, mesh_score, timestamp FROM mcp_mesh_scores WHERE server_id IN ({})".format(
                    ",".join(map(str, server_ids))
                )
            }
        )
        response.raise_for_status()
        return [MeshScore(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory(server_ids: List[int], db: Session = Depends(get_session)) -> List[MeshMemory]:
    """Fetch mesh memory for given server IDs from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT server_id, memory, timestamp FROM mesh_memory WHERE server_id IN ({})".format(
                    ",".join(map(str, server_ids))
                )
            }
        )
        response.raise_for_status()
        return [MeshMemory(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def reset_server_export_api_quarantine_endpoint(server_id: int, db: Session = Depends(get_session)) -> bool:
    """Reset the export API quarantine status for a server."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server.export_api_quarantine = False
    db.commit()
    return True

if __name__ == "__main__":
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data
    test_server_ids = [1, 2, 3]

    # Test get_signal_scores
    try:
        scores = get_signal_scores(test_server_ids)
        print(f"Signal scores test: {'PASS' if scores else 'FAIL'}")
    except Exception as e:
        print(f"Signal scores test: FAIL - {str(e)}")

    # Test get_mesh_scores
    try:
        scores = get_mesh_scores(test_server_ids)
        print(f"Mesh scores test: {'PASS' if scores else 'FAIL'}")
    except Exception as e:
        print(f"Mesh scores test: FAIL - {str(e)}")

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory(test_server_ids)
        print(f"Mesh memory test: {'PASS' if memory else 'FAIL'}")
    except Exception as e:
        print(f"Mesh memory test: FAIL - {str(e)}")

    # Test reset_server_export_api_quarantine_endpoint
    try:
        result = reset_server_export_api_quarantine_endpoint(1)
        print(f"Reset quarantine test: {'PASS' if result else 'FAIL'}")
    except Exception as e:
        print(f"Reset quarantine test: FAIL - {str(e)}")

    print("PASS")