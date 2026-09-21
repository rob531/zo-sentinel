from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
from pydantic import BaseModel
import requests

class ServerRegistryResponse(BaseModel):
    id: int
    hostname: str
    confidence: float
    description: Optional[str]

class AxisScoreResponse(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float

class ScoreDisputeResponse(BaseModel):
    id: int
    user_id: int
    server_id: int
    axis: str
    disputed_score: float
    reason: str

class MeshMemoryResponse(BaseModel):
    id: int
    data: dict

def mesh_memory_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Mesh memory query failed")
    return [MeshMemoryResponse(**item) for item in response.json()]

def get_mesh_memory_by_id(id: int):
    response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {id}"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Mesh memory query failed")
    result = response.json()
    if not result:
        raise HTTPException(status_code=404, detail="Mesh memory not found")
    return MeshMemoryResponse(**result[0])

def signal_scores_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Signal scores query failed")
    return response.json()

def get_server_registry(db: Session = Depends(get_session)):
    servers = db.query(McpServerRegistry).all()
    return [ServerRegistryResponse(**server.__dict__) for server in servers]

def get_axis_scores(db: Session = Depends(get_session)):
    scores = db.query(McpLlmAxisScore).all()
    return [AxisScoreResponse(**score.__dict__) for score in scores]

def get_score_disputes(db: Session = Depends(get_session)):
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDisputeResponse(**dispute.__dict__) for dispute in disputes]

if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    @app.get("/test")
    def test():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)