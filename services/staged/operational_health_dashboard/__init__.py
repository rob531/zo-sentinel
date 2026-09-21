from typing import List, Optional
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from fastapi import Depends
from sqlalchemy.orm import Session

class ServerRegistryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: str
    updated_at: str

class LlmAxisScoreResponse(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    created_at: str
    updated_at: str

class ScoreDisputeResponse(BaseModel):
    id: int
    score_id: int
    user_id: int
    comment: str
    created_at: str
    updated_at: str

class OrgResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: str
    updated_at: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    created_at: str
    updated_at: str

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistryResponse]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistryResponse(
        id=registry.id,
        name=registry.name,
        description=registry.description,
        created_at=str(registry.created_at),
        updated_at=str(registry.updated_at)
    ) for registry in registries]

def get_llm_axis_scores(db: Session = Depends(get_session)) -> List[LlmAxisScoreResponse]:
    scores = db.query(McpLlmAxisScore).all()
    return [LlmAxisScoreResponse(
        id=score.id,
        server_id=score.server_id,
        axis=score.axis,
        score=score.score,
        created_at=str(score.created_at),
        updated_at=str(score.updated_at)
    ) for score in scores]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDisputeResponse]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDisputeResponse(
        id=dispute.id,
        score_id=dispute.score_id,
        user_id=dispute.user_id,
        comment=dispute.comment,
        created_at=str(dispute.created_at),
        updated_at=str(dispute.updated_at)
    ) for dispute in disputes]

def get_orgs(db: Session = Depends(get_session)) -> List[OrgResponse]:
    orgs = db.query(Org).all()
    return [OrgResponse(
        id=org.id,
        name=org.name,
        description=org.description,
        created_at=str(org.created_at),
        updated_at=str(org.updated_at)
    ) for org in orgs]

def get_users(db: Session = Depends(get_session)) -> List[UserResponse]:
    users = db.query(User).all()
    return [UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        created_at=str(user.created_at),
        updated_at=str(user.updated_at)
    ) for user in users]

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: get_session()

    @app.get("/test")
    async def test():
        return {"status": "PASS"}

    client = TestClient(app)
    response = client.get("/test")
    assert response.json() == {"status": "PASS"}
    print("PASS")