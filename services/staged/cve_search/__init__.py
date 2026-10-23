from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()

class ServerExportAPIQuarantineRequest(BaseModel):
    server_id: int

class ServerExportAPIQuarantineResponse(BaseModel):
    success: bool
    message: str

class MeshMemoryRequest(BaseModel):
    server_ids: List[int]

class MeshMemoryResponse(BaseModel):
    mesh_memory: List[dict]

class MeshScoresRequest(BaseModel):
    server_ids: List[int]

class MeshScoresResponse(BaseModel):
    mesh_scores: List[dict]

class SignalScoresRequest(BaseModel):
    server_ids: List[int]

class SignalScoresResponse(BaseModel):
    signal_scores: List[dict]

def reset_server_export_api_quarantine(server_id: int) -> ServerExportAPIQuarantineResponse:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/reset_server_export_api_quarantine",
            json={"server_id": server_id},
            timeout=10
        )
        response.raise_for_status()
        return ServerExportAPIQuarantineResponse(
            success=True,
            message="Server export API quarantine reset successfully"
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory(server_ids: List[int]) -> MeshMemoryResponse:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory WHERE server_id IN :server_ids",
                  "params": {"server_ids": server_ids}},
            timeout=10
        )
        response.raise_for_status()
        return MeshMemoryResponse(mesh_memory=response.json())
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_scores(server_ids: List[int]) -> MeshScoresResponse:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores WHERE server_id IN :server_ids",
                  "params": {"server_ids": server_ids}},
            timeout=10
        )
        response.raise_for_status()
        return MeshScoresResponse(mesh_scores=response.json())
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores(server_ids: List[int]) -> SignalScoresResponse:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores WHERE server_id IN :server_ids",
                  "params": {"server_ids": server_ids}},
            timeout=10
        )
        response.raise_for_status()
        return SignalScoresResponse(signal_scores=response.json())
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reset_server_export_api_quarantine", response_model=ServerExportAPIQuarantineResponse)
async def reset_server_export_api_quarantine_endpoint(request: ServerExportAPIQuarantineRequest):
    return reset_server_export_api_quarantine(request.server_id)

@router.post("/mesh_memory", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint(request: MeshMemoryRequest):
    return get_mesh_memory(request.server_ids)

@router.post("/mesh_scores", response_model=MeshScoresResponse)
async def mesh_scores_endpoint(request: MeshScoresRequest):
    return get_mesh_scores(request.server_ids)

@router.post("/signal_scores", response_model=SignalScoresResponse)
async def signal_scores_endpoint(request: SignalScoresRequest):
    return get_signal_scores(request.server_ids)

def _run_self_test():
    from app.db import get_session
    from app.models import McpServerRegistry
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test tables
    McpServerRegistry.metadata.create_all(engine)

    # Test data
    test_server = McpServerRegistry(id=1, hostname="test-server")
    with SessionLocal() as session:
        session.add(test_server)
        session.commit()

    # Test reset_server_export_api_quarantine
    try:
        response = reset_server_export_api_quarantine(1)
        assert response.success is True
    except Exception as e:
        print(f"Test failed: {str(e)}")
        return

    # Test get_mesh_memory
    try:
        response = get_mesh_memory([1])
        assert isinstance(response.mesh_memory, list)
    except Exception as e:
        print(f"Test failed: {str(e)}")
        return

    # Test get_mesh_scores
    try:
        response = get_mesh_scores([1])
        assert isinstance(response.mesh_scores, list)
    except Exception as e:
        print(f"Test failed: {str(e)}")
        return

    # Test get_signal_scores
    try:
        response = get_signal_scores([1])
        assert isinstance(response.signal_scores, list)
    except Exception as e:
        print(f"Test failed: {str(e)}")
        return

    print("PASS")

if __name__ == "__main__":
    _run_self_test()