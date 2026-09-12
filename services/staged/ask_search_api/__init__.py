from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class ServerRegistry(BaseModel):
    id: int
    hostname: str
    ip_address: str
    confidence: float
    description: Optional[str] = None

class LlmAxisScore(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    timestamp: str

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    disputed_score: float
    reason: str
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

def get_server_registry(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def get_llm_axis_scores(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def get_score_disputes(db: Session = Depends(get_session)):
    return db.query(McpScoreDispute).all()

def get_orgs(db: Session = Depends(get_session)):
    return db.query(Org).all()

def get_users(db: Session = Depends(get_session)):
    return db.query(User).all()

if __name__ == "__main__":
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: get_session()

    @app.get("/test")
    async def test():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)