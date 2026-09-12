from fastapi import FastAPI, Depends, HTTPException
from typing import List, Dict, Optional
from pydantic import BaseModel
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from sqlalchemy.orm import Session

app = FastAPI()

class SignalScoresRequest(BaseModel):
    server_id: int
    org_id: Optional[int] = None

class MeshScoresRequest(BaseModel):
    server_id: int
    org_id: Optional[int] = None

class MeshMemoryRequest(BaseModel):
    server_id: int
    org_id: Optional[int] = None

class ResetQuarantineRequest(BaseModel):
    server_id: int
    org_id: Optional[int] = None

class SignalScoresResponse(BaseModel):
    server_id: int
    scores: Dict[str, float]

class MeshScoresResponse(BaseModel):
    server_id: int
    scores: Dict[str, float]

class MeshMemoryResponse(BaseModel):
    server_id: int
    memory: Dict[str, float]

class ResetQuarantineResponse(BaseModel):
    server_id: int
    success: bool

def get_signal_scores(server_id: int, org_id: Optional[int] = None, db: Session = Depends(get_session)) -> Dict[str, float]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id} AND org_id = {org_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_scores(server_id: int, org_id: Optional[int] = None, db: Session = Depends(get_session)) -> Dict[str, float]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id} AND org_id = {org_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory(server_id: int, org_id: Optional[int] = None, db: Session = Depends(get_session)) -> Dict[str, float]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id} AND org_id = {org_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def reset_server_export_api_quarantine(server_id: int, org_id: Optional[int] = None, db: Session = Depends(get_session)) -> bool:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"UPDATE McpServerRegistry SET quarantine = false WHERE server_id = {server_id} AND org_id = {org_id}"},
            timeout=10
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/signal_scores", response_model=SignalScoresResponse)
async def signal_scores_endpoint(request: SignalScoresRequest, db: Session = Depends(get_session)):
    scores = get_signal_scores(request.server_id, request.org_id, db)
    return {"server_id": request.server_id, "scores": scores}

@app.post("/mesh_scores", response_model=MeshScoresResponse)
async def mesh_scores_endpoint(request: MeshScoresRequest, db: Session = Depends(get_session)):
    scores = get_mesh_scores(request.server_id, request.org_id, db)
    return {"server_id": request.server_id, "scores": scores}

@app.post("/mesh_memory", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint(request: MeshMemoryRequest, db: Session = Depends(get_session)):
    memory = get_mesh_memory(request.server_id, request.org_id, db)
    return {"server_id": request.server_id, "memory": memory}

@app.post("/reset_quarantine", response_model=ResetQuarantineResponse)
async def reset_quarantine_endpoint(request: ResetQuarantineRequest, db: Session = Depends(get_session)):
    success = reset_server_export_api_quarantine(request.server_id, request.org_id, db)
    return {"server_id": request.server_id, "success": success}

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    app.dependency_overrides[get_session] = lambda: test_session

    try:
        test_server = McpServerRegistry(server_id=1, org_id=1, quarantine=False)
        test_session.add(test_server)
        test_session.commit()

        test_signal_scores = get_signal_scores(1, 1, test_session)
        test_mesh_scores = get_mesh_scores(1, 1, test_session)
        test_mesh_memory = get_mesh_memory(1, 1, test_session)
        test_reset = reset_server_export_api_quarantine(1, 1, test_session)

        if test_signal_scores and test_mesh_scores and test_mesh_memory and test_reset:
            print("PASS")
        else:
            print("FAIL")
    finally:
        test_session.close()
        app.dependency_overrides.clear()

if __name__ == "__main__":
    _run_self_test()