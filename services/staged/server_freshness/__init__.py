from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests
from pydantic import BaseModel

app = FastAPI()

class ServerRegistryResponse(BaseModel):
    id: int
    hostname: str
    confidence: float
    last_seen: str

class ScoreResponse(BaseModel):
    server_id: int
    axis: str
    score: float
    timestamp: str

class DisputeResponse(BaseModel):
    id: int
    score_id: int
    user_id: int
    comment: str
    timestamp: str

class OrgResponse(BaseModel):
    id: int
    name: str

class UserResponse(BaseModel):
    id: int
    username: str
    org_id: int

@app.get("/servers/", response_model=List[ServerRegistryResponse])
def get_servers(db: Session = Depends(get_session)):
    servers = db.query(McpServerRegistry).all()
    return [{"id": s.id, "hostname": s.hostname, "confidence": s.confidence, "last_seen": s.last_seen} for s in servers]

@app.get("/scores/", response_model=List[ScoreResponse])
def get_scores(db: Session = Depends(get_session)):
    scores = db.query(McpLlmAxisScore).all()
    return [{"server_id": s.server_id, "axis": s.axis, "score": s.score, "timestamp": s.timestamp} for s in scores]

@app.get("/disputes/", response_model=List[DisputeResponse])
def get_disputes(db: Session = Depends(get_session)):
    disputes = db.query(McpScoreDispute).all()
    return [{"id": d.id, "score_id": d.score_id, "user_id": d.user_id, "comment": d.comment, "timestamp": d.timestamp} for d in disputes]

@app.get("/orgs/", response_model=List[OrgResponse])
def get_orgs(db: Session = Depends(get_session)):
    orgs = db.query(Org).all()
    return [{"id": o.id, "name": o.name} for o in orgs]

@app.get("/users/", response_model=List[UserResponse])
def get_users(db: Session = Depends(get_session)):
    users = db.query(User).all()
    return [{"id": u.id, "username": u.username, "org_id": u.org_id} for u in users]

def signal_scores_endpoint():
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print("PASS")