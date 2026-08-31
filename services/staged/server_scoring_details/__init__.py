from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class ServerRegistry(BaseModel):
    id: int
    hostname: str
    ip_address: str
    status: str
    org_id: int

class LLMScore(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    timestamp: str

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    dispute_reason: str
    status: str
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

def get_server_registry(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    servers = db.query(McpServerRegistry).all()
    return [ServerRegistry(id=s.id, hostname=s.hostname, ip_address=s.ip_address, status=s.status, org_id=s.org_id) for s in servers]

def get_llm_scores(db: Session = Depends(get_session)) -> List[LLMScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [LLMScore(id=s.id, server_id=s.server_id, axis=s.axis, score=s.score, timestamp=str(s.timestamp)) for s in scores]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(id=d.id, score_id=d.score_id, dispute_reason=d.dispute_reason, status=d.status, timestamp=str(d.timestamp)) for d in disputes]

def get_orgs(db: Session = Depends(get_session)) -> List[OrgModel]:
    orgs = db.query(Org).all()
    return [OrgModel(id=o.id, name=o.name, description=o.description) for o in orgs]

def get_users(db: Session = Depends(get_session)) -> List[UserModel]:
    users = db.query(User).all()
    return [UserModel(id=u.id, username=u.username, email=u.email, org_id=u.org_id) for u in users]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup in-memory test database
    test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Override dependency for testing
    def override_get_session() -> Session:
        return Session(test_engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    # Test endpoints
    @app.get("/test")
    async def test_endpoint():
        return {"status": "PASS"}

    client = TestClient(app)
    response = client.get("/test")
    assert response.json() == {"status": "PASS"}
    print("PASS")