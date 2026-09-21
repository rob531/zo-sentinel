from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

class ServerRegistry(BaseModel):
    server_name: str
    server_ip: str
    server_status: str
    last_heartbeat: Optional[str] = None

class LLMScore(BaseModel):
    server_id: int
    axis: str
    score: float
    timestamp: str

class ScoreDispute(BaseModel):
    dispute_id: int
    server_id: int
    axis: str
    original_score: float
    disputed_score: float
    reason: str
    status: str

class OrgInfo(BaseModel):
    org_id: int
    org_name: str
    contact_email: str

class UserInfo(BaseModel):
    user_id: int
    username: str
    email: str
    org_id: int

class SentinelService:
    def __init__(self):
        self.app = FastAPI()

        @self.app.get("/servers")
        async def get_servers(db: Session = Depends(get_session)):
            servers = db.query(McpServerRegistry).all()
            return [{"server_name": s.server_name, "server_ip": s.server_ip, "server_status": s.server_status} for s in servers]

        @self.app.get("/scores")
        async def get_scores(db: Session = Depends(get_session)):
            scores = db.query(McpLlmAxisScore).all()
            return [{"server_id": s.server_id, "axis": s.axis, "score": s.score} for s in scores]

        @self.app.get("/disputes")
        async def get_disputes(db: Session = Depends(get_session)):
            disputes = db.query(McpScoreDispute).all()
            return [{"dispute_id": d.dispute_id, "server_id": d.server_id, "axis": d.axis, "original_score": d.original_score} for d in disputes]

        @self.app.get("/orgs")
        async def get_orgs(db: Session = Depends(get_session)):
            orgs = db.query(Org).all()
            return [{"org_id": o.id, "org_name": o.name, "contact_email": o.contact_email} for o in orgs]

        @self.app.get("/users")
        async def get_users(db: Session = Depends(get_session)):
            users = db.query(User).all()
            return [{"user_id": u.id, "username": u.username, "email": u.email, "org_id": u.org_id} for u in users]

if __name__ == "__main__":
    service = SentinelService()
    import uvicorn
    uvicorn.run(service.app, host="127.0.0.1", port=8000)
    print("PASS")