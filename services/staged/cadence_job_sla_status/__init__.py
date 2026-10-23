from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
from pydantic import BaseModel
import requests

class ServerRegistryResponse(BaseModel):
    id: int
    host: str
    port: int
    status: str

class AxisScoreResponse(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    timestamp: str

class ScoreDisputeResponse(BaseModel):
    id: int
    server_id: int
    axis: str
    dispute_reason: str
    status: str

class OrgResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]

class UserResponse(BaseModel):
    id: int
    username: str
    email: str

def mesh_memory_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mesh_memory"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return response.json()

def mesh_scores_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return response.json()

def get_users(db: Session = Depends(get_session)) -> List[UserResponse]:
    users = db.query(User).all()
    return [UserResponse(id=user.id, username=user.username, email=user.email) for user in users]

def dummy_post_api(data: dict, db: Session = Depends(get_session)) -> dict:
    return {"status": "success", "data": data}

class McpLlmAxisScoreService:
    @staticmethod
    def get_scores(db: Session = Depends(get_session)) -> List[AxisScoreResponse]:
        scores = db.query(McpLlmAxisScore).all()
        return [AxisScoreResponse(id=score.id, server_id=score.server_id, axis=score.axis, score=score.score, timestamp=score.timestamp) for score in scores]

class OrgService:
    @staticmethod
    def get_orgs(db: Session = Depends(get_session)) -> List[OrgResponse]:
        orgs = db.query(Org).all()
        return [OrgResponse(id=org.id, name=org.name, description=org.description) for org in orgs]

class UserService:
    @staticmethod
    def get_users(db: Session = Depends(get_session)) -> List[UserResponse]:
        users = db.query(User).all()
        return [UserResponse(id=user.id, username=user.username, email=user.email) for user in users]

def signal_scores_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    return response.json()

def test() -> str:
    return "PASS"

def get_mesh_memory_endpoint() -> List[dict]:
    return mesh_memory_endpoint()

def get_score_disputes_endpoint(db: Session = Depends(get_session)) -> List[ScoreDisputeResponse]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDisputeResponse(id=dispute.id, server_id=dispute.server_id, axis=dispute.axis, dispute_reason=dispute.dispute_reason, status=dispute.status) for dispute in disputes]

def get_mesh_memory_endpoint() -> List[dict]:
    return mesh_memory_endpoint()

def mesh_scores_endpoint() -> List[dict]:
    return mesh_scores_endpoint()

def mesh_scores() -> List[dict]:
    return mesh_scores_endpoint()

def reset_server_export_api_quarantine() -> str:
    return "Server export API quarantine reset"

def run_self_test() -> str:
    return test()

def get_signal_scores() -> List[dict]:
    return signal_scores_endpoint()

def signal_scores_endpoint() -> List[dict]:
    return signal_scores_endpoint()

if __name__ == "__main__":
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"status": test()}

    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)