from fastapi import FastAPI, Depends, APIRouter
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Base, McpLlmAxisScore, ServiceHealth

router = APIRouter()

class DaemonHealth(BaseModel):
    name: str
    status: str
    last_heartbeat: Optional[datetime]

class ScoringConsumerStatus(BaseModel):
    last_event_at: Optional[datetime]
    total_events: int
    latest_server_id: Optional[str]

class StatusResponse(BaseModel):
    scoring_consumer: ScoringConsumerStatus
    daemon_health: List[DaemonHealth]
    health_status: str

@router.get("/scoring/status", response_model=StatusResponse)
def get_scoring_status(db: Session = Depends(get_session)):
    # 1. Consumer Health from mcp_llm_axis_scores
    latest_score = db.query(McpLlmAxisScore).order_by(McpLlmAxisScore.scored_at.desc()).first()
    total_events = db.query(func.count(McpLlmAxisScore.id)).scalar() or 0
    
    last_event_at = latest_score.scored_at if latest_score else None
    latest_server_id = latest_score.server_id if latest_score else None

    # 2. Daemon Health
    target_daemons = ["app_scoring_consumer", "inference_router", "signal_analyser", "trust_synthesiser"]
    daemons = db.query(ServiceHealth).filter(ServiceHealth.name.in_(target_daemons)).all()
    
    daemon_list = [
        DaemonHealth(name=d.name, status=d.status, last_heartbeat=d.last_heartbeat) 
        for d in daemons
    ]

    # 3. Derive Overall Health Status
    # Logic: degraded if any daemon is not healthy; stale if no score in last 10 mins; else ok
    health_status = "ok"
    if any(d.status != "healthy" for d in daemons):
        health_status = "degraded"
    elif last_event_at is None or (datetime.utcnow() - last_event_at) > timedelta(minutes=10):
        health_status = "stale"

    return StatusResponse(
        scoring_consumer=ScoringConsumerStatus(
            last_event_at=last_event_at,
            total_events=total_events,
            latest_server_id=latest_server_id
        ),
        daemon_health=daemon_list,
        health_status=health_status
    )

app = FastAPI()
app.include_router(router)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite for self-test
    test_engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    # Seed data
    with TestingSessionLocal() as db:
        # Seed Service Health
        daemons = ["app_scoring_consumer", "inference_router", "signal_analyser", "trust_synthesiser"]
        for name in daemons:
            db.add(ServiceHealth(name=name, status="healthy", last_heartbeat=datetime.utcnow()))
        
        # Seed Scoring Data
        db.add(McpLlmAxisScore(server_id="srv-001", scored_at=datetime.utcnow()))
        db.commit()

    response = client.get("/scoring/status")
    assert response.status_code == 200
    data = response.json()
    
    assert "daemon_health" in data
    assert "health_status" in data
    assert len(data["daemon_health"]) > 0
    assert data["health_status"] in ["ok", "degraded", "stale"]
    
    print("PASS")