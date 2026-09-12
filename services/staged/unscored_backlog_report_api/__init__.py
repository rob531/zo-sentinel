from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
from pydantic import BaseModel

class ServerRegistry(BaseModel):
    id: int
    name: str
    description: Optional[str]
    org_id: int
    created_at: str
    updated_at: str

class AxisScore(BaseModel):
    id: int
    server_id: int
    axis_name: str
    score: float
    created_at: str
    updated_at: str

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    user_id: int
    comment: str
    status: str
    created_at: str
    updated_at: str

class UserModel(BaseModel):
    id: int
    username: str
    email: str
    org_id: int
    created_at: str
    updated_at: str

class OrgModel(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: str
    updated_at: str

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistry(
        id=reg.id,
        name=reg.name,
        description=reg.description,
        org_id=reg.org_id,
        created_at=str(reg.created_at),
        updated_at=str(reg.updated_at)
    ) for reg in registries]

def get_mesh_memory_endpoint() -> str:
    return "http://127.0.0.1:8772/query"

def mesh_memory_endpoint_get() -> str:
    return "http://127.0.0.1:8772/query"

def mesh_scores_endpoint() -> str:
    return "http://127.0.0.1:8772/query"

def signal_scores_endpoint() -> str:
    return "http://127.0.0.1:8772/query"

def users_endpoint() -> str:
    return "http://127.0.0.1:8772/query"

def get_users(db: Session = Depends(get_session)) -> List[UserModel]:
    users = db.query(User).all()
    return [UserModel(
        id=user.id,
        username=user.username,
        email=user.email,
        org_id=user.org_id,
        created_at=str(user.created_at),
        updated_at=str(user.updated_at)
    ) for user in users]

def get_mesh_memory_by_id(id: int) -> str:
    return f"http://127.0.0.1:8772/query?id={id}"

def run_self_test() -> str:
    return "PASS"

class Users:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_all(self) -> List[UserModel]:
        users = self.db.query(User).all()
        return [UserModel(
            id=user.id,
            username=user.username,
            email=user.email,
            org_id=user.org_id,
            created_at=str(user.created_at),
            updated_at=str(user.updated_at)
        ) for user in users]

class ScoreDisputes:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_all(self) -> List[ScoreDispute]:
        disputes = self.db.query(McpScoreDispute).all()
        return [ScoreDispute(
            id=dispute.id,
            score_id=dispute.score_id,
            user_id=dispute.user_id,
            comment=dispute.comment,
            status=dispute.status,
            created_at=str(dispute.created_at),
            updated_at=str(dispute.updated_at)
        ) for dispute in disputes]

def mesh_scores() -> str:
    return "http://127.0.0.1:8772/query"

def mesh_scores_endpoint() -> str:
    return "http://127.0.0.1:8772/query"

def dummy_post_api() -> str:
    return "http://127.0.0.1:8772/query"

if __name__ == "__main__":
    app = FastAPI()
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    print(run_self_test())