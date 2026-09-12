from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from sqlalchemy.orm import Session
import json

router = APIRouter()

class MeshMemoryRequest(BaseModel):
    server_id: int

class MeshMemoryResponse(BaseModel):
    mesh_memory: Optional[dict]

class MeshScoresRequest(BaseModel):
    server_ids: List[int]

class MeshScoresResponse(BaseModel):
    mesh_scores: Optional[dict]

class SignalScoresRequest(BaseModel):
    server_ids: List[int]

class SignalScoresResponse(BaseModel):
    signal_scores: Optional[dict]

def get_mesh_memory(server_id: int) -> dict:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_scores(server_ids: List[int]) -> dict:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN ({','.join(map(str, server_ids))})"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores(server_ids: List[int]) -> dict:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN ({','.join(map(str, server_ids))})"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mesh_memory", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint(request: MeshMemoryRequest):
    return {"mesh_memory": get_mesh_memory(request.server_id)}

@router.post("/mesh_scores", response_model=MeshScoresResponse)
async def mesh_scores_endpoint(request: MeshScoresRequest):
    return {"mesh_scores": get_mesh_scores(request.server_ids)}

@router.post("/signal_scores", response_model=SignalScoresResponse)
async def signal_scores_endpoint(request: SignalScoresRequest):
    return {"signal_scores": get_signal_scores(request.server_ids)}

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    test_server = McpServerRegistry(server_id=1, hostname="test.example.com")
    test_session.add(test_server)
    test_session.commit()

    try:
        memory = get_mesh_memory(1)
        scores = get_mesh_scores([1])
        signal = get_signal_scores([1])
        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")
    finally:
        test_session.close()
        app.dependency_overrides.pop(get_session, None)

if __name__ == "__main__":
    _run_self_test()