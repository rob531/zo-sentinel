from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, McpLlmAxisScore, Org, User
from pydantic import BaseModel
import requests

class ScoreDispute(BaseModel):
    id: int
    org_id: int
    user_id: int
    score_id: int
    reason: str
    status: str
    created_at: str
    updated_at: str

class ServerRegistry(BaseModel):
    id: int
    org_id: int
    hostname: str
    port: int
    status: str
    last_heartbeat: str

class LLMScore(BaseModel):
    id: int
    org_id: int
    axis_id: int
    score: float
    created_at: str

class OrgModel(BaseModel):
    id: int
    name: str
    description: str

class UserModel(BaseModel):
    id: int
    org_id: int
    name: str
    email: str

def get_score_disputes_endpoint(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(
        id=d.id,
        org_id=d.org_id,
        user_id=d.user_id,
        score_id=d.score_id,
        reason=d.reason,
        status=d.status,
        created_at=str(d.created_at),
        updated_at=str(d.updated_at)
    ) for d in disputes]

def get_mesh_memory_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()["data"]

def get_mesh_memory_by_id(id: int) -> Optional[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {id}"})
    data = response.json()["data"]
    return data[0] if data else None

def mesh_memory_endpoint() -> List[dict]:
    return get_mesh_memory_endpoint()

def users_endpoint(db: Session = Depends(get_session)) -> List[UserModel]:
    users = db.query(User).all()
    return [UserModel(
        id=u.id,
        org_id=u.org_id,
        name=u.name,
        email=u.email
    ) for u in users]

def signal_scores_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()["data"]

def test_self() -> str:
    return "PASS"

if __name__ == "__main__":
    app = FastAPI()

    app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=False, autoflush=False)

    @app.get("/test")
    def test():
        return test_self()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)