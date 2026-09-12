from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class ServerRegistry(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool

class LlmAxisScore(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    timestamp: str

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    user_id: int
    comment: str
    timestamp: str

class OrgModel(BaseModel):
    id: int
    name: str
    description: Optional[str] = None

class UserModel(BaseModel):
    id: int
    username: str
    email: str
    org_id: int

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistry(
        id=registry.id,
        name=registry.name,
        description=registry.description,
        is_active=registry.is_active
    ) for registry in registries]

def get_llm_axis_scores(db: Session = Depends(get_session)) -> List[LlmAxisScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [LlmAxisScore(
        id=score.id,
        server_id=score.server_id,
        axis=score.axis,
        score=score.score,
        timestamp=str(score.timestamp)
    ) for score in scores]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(
        id=dispute.id,
        score_id=dispute.score_id,
        user_id=dispute.user_id,
        comment=dispute.comment,
        timestamp=str(dispute.timestamp)
    ) for dispute in disputes]

def get_orgs(db: Session = Depends(get_session)) -> List[OrgModel]:
    orgs = db.query(Org).all()
    return [OrgModel(
        id=org.id,
        name=org.name,
        description=org.description
    ) for org in orgs]

def get_users(db: Session = Depends(get_session)) -> List[UserModel]:
    users = db.query(User).all()
    return [UserModel(
        id=user.id,
        username=user.username,
        email=user.email,
        org_id=user.org_id
    ) for user in users]

if __name__ == "__main__":
    app = FastAPI()

    @app.get("/test")
    def test():
        return "PASS"

    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)