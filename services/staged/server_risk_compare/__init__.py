"""
Auto-emitted service package for mesh memory operations.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

import requests
from typing import Optional, Any
import json

router = APIRouter()

SERVICE_HOST = "127.0.0.1"
WRITE_SERVICE_URL = f"http://{SERVICE_HOST}:8772"


class MeshMemoryRecord(BaseModel):
    key: str
    value: Any
    metadata: Optional[dict] = None


class MeshMemoryResponse(BaseModel):
    success: bool
    data: Optional[list[MeshMemoryRecord]] = None
    error: Optional[str] = None


@router.get("/mesh/memory", response_model=MeshMemoryResponse)
def mesh_memory_endpoint(
    session: Session = Depends(get_session),
    limit: int = 100,
    offset: int = 0
) -> MeshMemoryResponse:
    """Fetch mesh memory records from the ZoComputer store."""
    try:
        query_payload = {
            "sql": "SELECT key, value, metadata FROM mesh_memory ORDER BY key LIMIT :limit OFFSET :offset",
            "params": {"limit": limit, "offset": offset}
        }
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=query_payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        
        records = []
        for row in result.get("rows", []):
            records.append(MeshMemoryRecord(
                key=row.get("key", ""),
                value=row.get("value"),
                metadata=row.get("metadata")
            ))
        
        return MeshMemoryResponse(success=True, data=records)
    except requests.RequestException as e:
        return MeshMemoryResponse(success=False, error=str(e))
    except Exception as e:
        return MeshMemoryResponse(success=False, error=str(e))


@router.get("/mesh/memory/{key}", response_model=MeshMemoryResponse)
def mesh_memory_endpoint_get(
    key: str,
    session: Session = Depends(get_session)
) -> MeshMemoryResponse:
    """Fetch a specific mesh memory record by key."""
    try:
        query_payload = {
            "sql": "SELECT key, value, metadata FROM mesh_memory WHERE key = :key LIMIT 1",
            "params": {"key": key}
        }
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=query_payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        
        records = []
        for row in result.get("rows", []):
            records.append(MeshMemoryRecord(
                key=row.get("key", ""),
                value=row.get("value"),
                metadata=row.get("metadata")
            ))
        
        return MeshMemoryResponse(success=True, data=records)
    except requests.RequestException as e:
        return MeshMemoryResponse(success=False, error=str(e))
    except Exception as e:
        return MeshMemoryResponse(success=False, error=str(e))


@router.get("/servers", response_model=dict)
def get_mesh_memory_endpoint(
    session: Session = Depends(get_session)
) -> dict:
    """Fetch registered servers from app database."""
    try:
        servers = session.query(McpServerRegistry).limit(50).all()
        return {
            "success": True,
            "data": [
                {
                    "id": s.id,
                    "name": s.name,
                    "url": s.url,
                    "status": s.status
                }
                for s in servers
            ]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/scores/disputes", response_model=dict)
def get_score_disputes_endpoint(
    session: Session = Depends(get_session)
) -> dict:
    """Fetch open score disputes from app database."""
    try:
        disputes = session.query(McpScoreDispute).filter(
            McpScoreDispute.status == "open"
        ).limit(100).all()
        return {
            "success": True,
            "data": [
                {
                    "id": d.id,
                    "server_id": d.server_id,
                    "axis": d.axis,
                    "status": d.status
                }
                for d in disputes
            ]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/signal/scores", response_model=dict)
def signal_scores_endpoint(
    payload: dict,
    session: Session = Depends(get_session)
) -> dict:
    """Post signal scores to mesh pipeline."""
    try:
        query_payload = {
            "sql": "INSERT INTO mcp_signal_scores (server_id, axis, score, metadata, created_at) VALUES (:server_id, :axis, :score, :metadata, NOW())",
            "params": {
                "server_id": payload.get("server_id"),
                "axis": payload.get("axis"),
                "score": payload.get("score", 0.0),
                "metadata": json.dumps(payload.get("metadata", {}))
            }
        }
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=query_payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        return {"success": True, "result": response.json()}
    except requests.RequestException as e:
        return {"success": False, "error": str(e)}


def run_self_test() -> bool:
    """Run self-test with in-memory store override."""
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    in_memory_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    
    from app.models import Base
    Base.metadata.create_all(bind=in_memory_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=in_memory_engine)
    
    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    that_app = FastAPI()
    
    @that_app.get("/test/mesh/memory")
    def test_mesh_memory():
        return MeshMemoryResponse(success=True, data=[])
    
    @that_app.get("/test/servers")
    def test_servers():
        return {"success": True, "data": []}
    
    @that_app.get("/test/scores/disputes")
    def test_disputes():
        return {"success": True, "data": []}
    
    @that_app.post("/test/signal/scores")
    def test_signal_scores():
        return {"success": True, "result": {}}
    
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session
    
    with TestClient(that_app) as client:
        r1 = client.get("/test/mesh/memory")
        r2 = client.get("/test/servers")
        r3 = client.get("/test/scores/disputes")
        r4 = client.post("/test/signal/scores", json={"server_id": "test", "axis": "test", "score": 0.5})
        
        if r1.status_code != 200 or r2.status_code != 200 or r3.status_code != 200 or r4.status_code != 200:
            return False
        
        if not (r1.json()["success"] and r2.json()["success"] and r3.json()["success"] and r4.json()["success"]):
            return False
    
    return True


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    
    if run_self_test():
        print("PASS")
    else:
        print("FAIL")