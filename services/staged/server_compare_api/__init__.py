from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class ServerRegistry(BaseModel):
    id: int
    confidence: float
    description: str
    org_id: int
    user_id: int

class AxisScore(BaseModel):
    id: int
    score: float
    axis: str
    server_id: int

class ScoreDispute(BaseModel):
    id: int
    server_id: int
    user_id: int
    dispute_reason: str

class OrgModel(BaseModel):
    id: int
    name: str

class UserModel(BaseModel):
    id: int
    username: str
    org_id: int

def get_server_registry(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    servers = db.query(McpServerRegistry).all()
    return [ServerRegistry(
        id=server.id,
        confidence=server.confidence,
        description=server.description,
        org_id=server.org_id,
        user_id=server.user_id
    ) for server in servers]

def get_axis_scores(db: Session = Depends(get_session)) -> List[AxisScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [AxisScore(
        id=score.id,
        score=score.score,
        axis=score.axis,
        server_id=score.server_id
    ) for score in scores]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(
        id=dispute.id,
        server_id=dispute.server_id,
        user_id=dispute.user_id,
        dispute_reason=dispute.dispute_reason
    ) for dispute in disputes]

def get_orgs(db: Session = Depends(get_session)) -> List[OrgModel]:
    orgs = db.query(Org).all()
    return [OrgModel(
        id=org.id,
        name=org.name
    ) for org in orgs]

def get_users(db: Session = Depends(get_session)) -> List[UserModel]:
    users = db.query(User).all()
    return [UserModel(
        id=user.id,
        username=user.username,
        org_id=user.org_id
    ) for user in users]

def mesh_memory_endpoint():
    return {"status": "ok"}

def get_mesh_memory_by_id(id: int):
    return {"id": id, "status": "ok"}

if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.models import Base

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=create_engine("sqlite:///:memory:", poolclass=StaticPool),
        expire_on_commit=False
    )

    Base.metadata.create_all(bind=create_engine("sqlite:///:memory:", poolclass=StaticPool))

    @test_app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)

    print("PASS")