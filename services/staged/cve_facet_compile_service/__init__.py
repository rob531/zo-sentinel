from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory

class ServerRegistry(BaseModel):
    id: int
    hostname: str
    ip_address: str
    status: str

class LLMScore(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    timestamp: str

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    reason: str
    status: str

class VulnerabilityAdvisory(BaseModel):
    id: int
    cve_id: str
    description: str
    severity: str

def get_server_registries(db: Session = Depends(get_session)) -> List[ServerRegistry]:
    registries = db.query(McpServerRegistry).all()
    return [ServerRegistry(id=r.id, hostname=r.hostname, ip_address=r.ip_address, status=r.status) for r in registries]

def get_llm_scores(db: Session = Depends(get_session)) -> List[LLMScore]:
    scores = db.query(McpLlmAxisScore).all()
    return [LLMScore(id=s.id, server_id=s.server_id, axis=s.axis, score=s.score, timestamp=str(s.timestamp)) for s in scores]

def get_score_disputes(db: Session = Depends(get_session)) -> List[ScoreDispute]:
    disputes = db.query(McpScoreDispute).all()
    return [ScoreDispute(id=d.id, score_id=d.score_id, reason=d.reason, status=d.status) for d in disputes]

def get_vulnerability_advisories(db: Session = Depends(get_session)) -> List[VulnerabilityAdvisory]:
    advisories = db.query(VulnAdvisory).all()
    return [VulnerabilityAdvisory(id=a.id, cve_id=a.cve_id, description=a.description, severity=a.severity) for a in advisories]

if __name__ == "__main__":
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: get_session()

    @app.get("/test")
    async def test():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)