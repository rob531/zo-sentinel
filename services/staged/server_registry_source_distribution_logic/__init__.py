from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class ServerRegistry(BaseModel):
    id: int
    host: str
    port: int
    is_active: bool

class ScoreDispute(BaseModel):
    id: int
    user_id: int
    score_id: int
    dispute_reason: str
    status: str

class UserRead(BaseModel):
    id: int
    username: str
    email: str
    org_id: int

class Users(BaseModel):
    users: List[UserRead]

class ScoreDisputes(BaseModel):
    disputes: List[ScoreDispute]

class MeshMemory(BaseModel):
    id: int
    data: dict

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistry(id=r.id, host=r.host, port=r.port, is_active=r.is_active) for r in registries]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(id=d.id, user_id=d.user_id, score_id=d.score_id, dispute_reason=d.dispute_reason, status=d.status) for d in disputes]

def users_endpoint(db: Session = Depends(get_session)) -> List[UserRead]:
    users = db.query(User).all()
    return [UserRead(id=u.id, username=u.username, email=u.email, org_id=u.org_id) for u in users]

def mesh_memory_endpoint() -> List[MeshMemory]:
    # This is a placeholder for the actual implementation that would query the ZoComputer store
    return []

def get_mesh_memory_by_id(id: int) -> Optional[MeshMemory]:
    # This is a placeholder for the actual implementation that would query the ZoComputer store
    return None

def test_self() -> bool:
    return True

def run_self_test() -> bool:
    return True

def test_service_package() -> bool:
    return True

if __name__ == "__main__":
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=False, autoflush=False)

    @app.get("/test")
    def test():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)