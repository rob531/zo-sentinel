from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from pydantic import BaseModel
import requests

router = APIRouter()

class ServerRegistryResponse(BaseModel):
    id: int
    host: str
    port: int
    org_id: int
    is_active: bool

class AxisScoreResponse(BaseModel):
    id: int
    signal_id: str
    axis_id: int
    score: float
    created_at: str

class ScoreDisputeResponse(BaseModel):
    id: int
    score_id: int
    user_id: int
    dispute_reason: str
    status: str
    created_at: str

class MeshMemoryResponse(BaseModel):
    id: str
    data: dict
    created_at: str

def get_mesh_memory_by_id(id: str) -> MeshMemoryResponse:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE id = '{id}'"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    return MeshMemoryResponse(**response.json()[0])

def mesh_memory_endpoint() -> List[MeshMemoryResponse]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT * FROM mesh_memory"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    return [MeshMemoryResponse(**item) for item in response.json()]

@router.get("/server-registry", response_model=List[ServerRegistryResponse])
def get_server_registry(db: Session = Depends(get_session)):
    servers = db.query(McpServerRegistry).all()
    return [ServerRegistryResponse.from_orm(server) for server in servers]

@router.get("/axis-scores", response_model=List[AxisScoreResponse])
def get_axis_scores(db: Session = Depends(get_session)):
    scores = db.query(McpLlmAxisScore).all()
    return [AxisScoreResponse.from_orm(score) for score in scores]

@router.get("/score-disputes", response_model=List[ScoreDisputeResponse])
def get_score_disputes(db: Session = Depends(get_session)):
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDisputeResponse.from_orm(dispute) for dispute in disputes]

@router.get("/mesh-memory/{id}", response_model=MeshMemoryResponse)
def get_mesh_memory(id: str):
    return get_mesh_memory_by_id(id)

@router.get("/mesh-memory", response_model=List[MeshMemoryResponse])
def get_all_mesh_memory():
    return mesh_memory_endpoint()

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)

    print("PASS")