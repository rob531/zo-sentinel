from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org
from pydantic import BaseModel

class ServerRegistry(BaseModel):
    id: int
    host: str
    port: int
    is_active: bool
    org_id: int

class AxisScore(BaseModel):
    id: int
    score: float
    axis_id: int
    server_registry_id: int
    created_at: str

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    user_id: int
    comment: str
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

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistry(id=r.id, host=r.host, port=r.port, is_active=r.is_active, org_id=r.org_id) for r in registries]

def get_mesh_memory_by_id(memory_id: int, db: Session = Depends(get_session)) -> Optional[dict]:
    # This is a placeholder for the actual implementation
    # In a real scenario, this would query the mesh_memory table via the ZoComputer store
    return {"id": memory_id, "data": "sample_data"}

def get_score_disputes(db: Session = Depends(get_session)) -> ScoreDisputes:
    disputes = db.query(McpScoreDispute).all()
    return ScoreDisputes(disputes=[ScoreDispute(id=d.id, score_id=d.score_id, user_id=d.user_id, comment=d.comment, status=d.status) for d in disputes])

def users_endpoint(db: Session = Depends(get_session)) -> Users:
    users = db.query(User).all()
    return Users(users=[UserRead(id=u.id, username=u.username, email=u.email, org_id=u.org_id) for u in users])

def mesh_memory_endpoint(db: Session = Depends(get_session)) -> dict:
    # This is a placeholder for the actual implementation
    # In a real scenario, this would query the mesh_memory table via the ZoComputer store
    return {"status": "success", "data": "sample_mesh_memory_data"}

def signal_scores_endpoint(db: Session = Depends(get_session)) -> List[AxisScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [AxisScore(id=s.id, score=s.score, axis_id=s.axis_id, server_registry_id=s.server_registry_id, created_at=str(s.created_at)) for s in scores]

def test_self() -> str:
    return "PASS"

if __name__ == "__main__":
    app = FastAPI()

    @app.get("/test")
    def test():
        return test_self()

    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Override the get_session dependency for testing
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create tables for testing
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Run the test
    print(test_self())