from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/verdicts", tags=["verdicts"])


class VerdictChange(BaseModel):
    server_id: str
    name: str
    old_tier: Optional[str]
    new_tier: str
    changed_at: datetime
    confidence: Optional[float]


class VerdictTrajectory(BaseModel):
    server_id: str
    name: str
    history: List[dict]


@router.get("/changes", response_model=List[VerdictChange])
def get_verdict_changes(
    hours: int = Query(default=24, ge=1),
    risk_tier: Optional[str] = Query(default=None),
    session: Session = Depends(get_session)
):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    
    latest_subq = (
        select(
            MCPLLMAxisScores.server_id,
            func.max(MCPLLMAxisScores.scored_at).label("max_scored_at")
        )
        .where(MCPLLMAxisScores.scored_at >= cutoff)
        .group_by(MCPLLMAxisScores.server_id)
        .subquery()
    )
    
    latest_scores_q = (
        select(MCPLLMAxisScores)
        .join(
            latest_subq,
            and_(
                MCPLLMAxisScores.server_id == latest_subq.c.server_id,
                MCPLLMAxisScores.scored_at == latest_subq.c.max_scored_at
            )
        )
    )
    
    if risk_tier:
        latest_scores_q = latest_scores_q.where(MCPLLMAxisScores.risk_tier == risk_tier)
    
    latest_scores = session.execute(latest_scores_q).scalars().all()
    
    prev_subq = (
        select(
            MCPLLMAxisScores.server_id,
            func.max(MCPLLMAxisScores.scored_at).label("max_prev_at")
        )
        .where(MCPLLMAxisScores.scored_at < cutoff)
        .group_by(MCPLLMAxisScores.server_id)
        .subquery()
    )
    
    prev_scores_q = select(MCPLLMAxisScores).join(
        prev_subq,
        and_(
            MCPLLMAxisScores.server_id == prev_subq.c.server_id,
            MCPLLMAxisScores.scored_at == prev_subq.c.max_prev_at
        )
    )
    prev_scores = {ps.server_id: ps for ps in session.execute(prev_scores_q).scalars().all()}
    
    changes = []
    for score in latest_scores:
        prev = prev_scores.get(score.server_id)
        old_tier = prev.risk_tier if prev and prev.risk_tier != score.risk_tier else None
        
        if old_tier is None and prev and prev.risk_tier == score.risk_tier:
            continue
        
        changes.append({
            "server_id": score.server_id,
            "name": score.server.name if score.server else None,
            "old_tier": old_tier,
            "new_tier": score.risk_tier,
            "changed_at": score.scored_at,
            "confidence": score.confidence
        })
    
    return changes


@router.get("/servers/{server_id}/verdict-trajectory", response_model=VerdictTrajectory)
def get_verdict_trajectory(
    server_id: str,
    hours: int = Query(default=24, ge=1),
    session: Session = Depends(get_session)
):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    
    server = session.execute(
        select(MCPServerRegistry).where(MCPServerRegistry.id == server_id)
    ).scalars().first()
    
    name = server.name if server else None
    
    scores = session.execute(
        select(MCPLLMAxisScores)
        .where(
            and_(
                MCPLLMAxisScores.server_id == server_id,
                MCPLLMAxisScores.scored_at >= cutoff
            )
        )
        .order_by(MCPLLMAxisScores.scored_at.desc())
    ).scalars().all()
    
    history = [
        {
            "risk_tier": s.risk_tier,
            "confidence": s.confidence,
            "scored_at": s.scored_at.isoformat()
        }
        for s in scores
    ]
    
    return {"server_id": server_id, "name": name, "history": history}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    
    app = FastAPI()
    app.include_router(router)
    
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    
    with TestingSession() as db:
        now = datetime.utcnow()
        
        srv1 = MCPServerRegistry(id="srv-001", name="alpha-server", url="http://alpha.local")
        srv2 = MCPServerRegistry(id="srv-002", name="beta-server", url="http://beta.local")
        srv3 = MCPServerRegistry(id="srv-003", name="gamma-server", url="http://gamma.local")
        db.add_all([srv1, srv2, srv3])
        db.flush()
        
        db.add(MCPLLMAxisScores(server_id="srv-001", risk_tier="LOW", confidence=0.95, scored_at=now - timedelta(hours=48)))
        db.add(MCPLLMAxisScores(server_id="srv-001", risk_tier="MEDIUM", confidence=0.88, scored_at=now - timedelta(hours=12)))
        db.add(MCPLLAxisScores(server_id="srv-001", risk_tier="HIGH", confidence=0.92, scored_at=now - timedelta(hours=2)))
        db.add(MCPLLMAxisScores(server_id="srv-002", risk_tier="LOW", confidence=0.99, scored_at=now - timedelta(hours=6)))
        db.add(MCPLLMAxisScores(server_id="srv-002", risk_tier="CRITICAL", confidence=0.85, scored_at=now - timedelta(hours=1)))
        db.add(MCPLLMAxisScores(server_id="srv-003", risk_tier="HIGH", confidence=0.90, scored_at=now - timedelta(hours=3)))
        db.commit()
    
    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    r1 = client.get("/verdicts/changes")
    assert r1.status_code == 200, f"changes status: {r1.status_code}"
    assert isinstance(r1.json(), list), "changes not a list"
    assert len(r1.json()) > 0, "changes empty"
    
    r2 = client.get("/servers/srv-001/verdict-trajectory")
    assert r2.status_code == 200, f"trajectory status: {r2.status_code}"
    assert isinstance(r2.json(), dict), "trajectory not a dict"
    assert "history" in r2.json(), "trajectory missing history"
    assert len(r2.json()["history"]) > 0, "trajectory history empty"
    
    r3 = client.get("/servers/srv-999/verdict-trajectory")
    assert r3.status_code == 200, f"missing server trajectory status: {r3.status_code}"
    assert r3.json()["history"] == [], "non-existent server history not empty"
    
    print("PASS")