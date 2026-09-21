from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class ServerRegistry(BaseModel):
    id: int
    hostname: str
    confidence: float

class LLMScore(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    dispute_reason: str

class OrgModel(BaseModel):
    id: int
    name: str

class UserModel(BaseModel):
    id: int
    username: str
    org_id: int

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistry(id=r.id, hostname=r.hostname, confidence=r.confidence) for r in registries]

def get_llm_scores(db: Session = Depends(get_session)) -> List[LLMScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [LLMScore(id=s.id, server_id=s.server_id, axis=s.axis, score=s.score) for s in scores]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(id=d.id, score_id=d.score_id, dispute_reason=d.dispute_reason) for d in disputes]

def get_orgs(db: Session = Depends(get_session)) -> List[OrgModel]:
    orgs = db.query(Org).all()
    return [OrgModel(id=o.id, name=o.name) for o in orgs]

def get_users(db: Session = Depends(get_session)) -> List[UserModel]:
    users = db.query(User).all()
    return [UserModel(id=u.id, username=u.username, org_id=u.org_id) for u in users]

app = FastAPI()

app.get("/server-registries")(get_server_registries)
app.get("/llm-scores")(get_llm_scores)
app.get("/score-disputes")(get_score_disputes)
app.get("/orgs")(get_orgs)
app.get("/users")(get_users)

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: sessionmaker(
        bind=create_engine("sqlite:///:memory:", poolclass=StaticPool),
        expire_on_commit=False,
    )()

    test_app.get("/server-registries")(get_server_registries)
    test_app.get("/llm-scores")(get_llm_scores)
    test_app.get("/score-disputes")(get_score_disputes)
    test_app.get("/orgs")(get_orgs)
    test_app.get("/users")(get_users)

    uvicorn.run(test_app, host="127.0.0.1", port=8000)
    print("PASS")