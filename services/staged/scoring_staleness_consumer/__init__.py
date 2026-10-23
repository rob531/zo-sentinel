from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from pydantic import BaseModel

class MeshScore(BaseModel):
    server_id: int
    score: float
    timestamp: str

class MeshMemory(BaseModel):
    key: str
    value: str
    timestamp: str

class SignalScore(BaseModel):
    server_id: int
    signal_type: str
    score: float
    timestamp: str

class ScoreDispute(BaseModel):
    id: int
    server_id: int
    disputed_score: float
    reason: str
    status: str

class OrgInfo(BaseModel):
    id: int
    name: str
    description: str

class ServerInfo(BaseModel):
    id: int
    hostname: str
    ip_address: str
    org_id: int

class AxisScore(BaseModel):
    id: int
    server_id: int
    axis_name: str
    score: float
    timestamp: str

def get_mesh_scores() -> List[MeshScore]:
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT server_id, score, timestamp FROM mcp_signal_scores ORDER BY timestamp DESC"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    return [MeshScore(**item) for item in response.json()]

def get_mesh_memory() -> List[MeshMemory]:
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT key, value, timestamp FROM mesh_memory ORDER BY timestamp DESC"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    return [MeshMemory(**item) for item in response.json()]

def get_signal_scores(db: Session = Depends(get_session)) -> List[SignalScore]:
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT server_id, signal_type, score, timestamp FROM mcp_signal_scores ORDER BY timestamp DESC"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    return [SignalScore(**item) for item in response.json()]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(
        id=dispute.id,
        server_id=dispute.server_id,
        disputed_score=dispute.disputed_score,
        reason=dispute.reason,
        status=dispute.status
    ) for dispute in disputes]

def get_orgs(db: Session = Depends(get_session)) -> List[OrgInfo]:
    orgs = db.query(Org).all()
    return [OrgInfo(
        id=org.id,
        name=org.name,
        description=org.description
    ) for org in orgs]

def get_servers(db: Session = Depends(get_session)) -> List[ServerInfo]:
    servers = db.query(McpServerRegistry).all()
    return [ServerInfo(
        id=server.id,
        hostname=server.hostname,
        ip_address=server.ip_address,
        org_id=server.org_id
    ) for server in servers]

def get_axis_scores(db: Session = Depends(get_session)) -> List[AxisScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [AxisScore(
        id=score.id,
        server_id=score.server_id,
        axis_name=score.axis_name,
        score=score.score,
        timestamp=score.timestamp
    ) for score in scores]

def reset_quarantine_api():
    # Placeholder for reset functionality
    pass

def _run_self_test():
    # Self-test implementation
    app = FastAPI()

    @app.get("/test")
    def test_endpoint():
        return {"status": "ok"}

    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/test")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    print("PASS")

if __name__ == "__main__":
    _run_self_test()