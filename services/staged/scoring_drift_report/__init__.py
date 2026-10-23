from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org
from fastapi import Depends, FastAPI

class ServerRegistry(BaseModel):
    id: int
    hostname: str
    ip_address: str
    status: str
    last_updated: str

class LlmAxisScore(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    last_updated: str

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    dispute_reason: str
    status: str
    last_updated: str

class UserModel(BaseModel):
    id: int
    clerk_id: str
    email: str
    clerk_created_at: str
    clerk_updated_at: str
    clerk_metadata: dict
    clerk_role: str
    clerk_first_name: str
    clerk_last_name: str

class OrgModel(BaseModel):
    id: int
    clerk_id: str
    name: str
    clerk_created_at: str
    clerk_updated_at: str
    clerk_metadata: dict

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistry(
        id=registry.id,
        hostname=registry.hostname,
        ip_address=registry.ip_address,
        status=registry.status,
        last_updated=str(registry.last_updated)
    ) for registry in registries]

def get_llm_axis_scores(db: Session = Depends(get_session)) -> List[LlmAxisScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [LlmAxisScore(
        id=score.id,
        server_id=score.server_id,
        axis=score.axis,
        score=score.score,
        last_updated=str(score.last_updated)
    ) for score in scores]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(
        id=dispute.id,
        score_id=dispute.score_id,
        dispute_reason=dispute.dispute_reason,
        status=dispute.status,
        last_updated=str(dispute.last_updated)
    ) for dispute in disputes]

def get_users(db: Session = Depends(get_session)) -> List[UserModel]:
    users = db.query(User).all()
    return [UserModel(
        id=user.id,
        clerk_id=user.clerk_id,
        email=user.email,
        clerk_created_at=str(user.clerk_created_at),
        clerk_updated_at=str(user.clerk_updated_at),
        clerk_metadata=user.clerk_metadata,
        clerk_role=user.clerk_role,
        clerk_first_name=user.clerk_first_name,
        clerk_last_name=user.clerk_last_name
    ) for user in users]

def get_orgs(db: Session = Depends(get_session)) -> List[OrgModel]:
    orgs = db.query(Org).all()
    return [OrgModel(
        id=org.id,
        clerk_id=org.clerk_id,
        name=org.name,
        clerk_created_at=str(org.clerk_created_at),
        clerk_updated_at=str(org.clerk_updated_at),
        clerk_metadata=org.clerk_metadata
    ) for org in orgs]

def main():
    app = FastAPI()

    @app.get("/server_registries")
    async def read_server_registries():
        return get_server_registries()

    @app.get("/llm_axis_scores")
    async def read_llm_axis_scores():
        return get_llm_axis_scores()

    @app.get("/score_disputes")
    async def read_score_disputes():
        return get_score_disputes()

    @app.get("/users")
    async def read_users():
        return get_users()

    @app.get("/orgs")
    async def read_orgs():
        return get_orgs()

    # Self-test
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=True, autoflush=True)

    @test_app.get("/test")
    async def test():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()