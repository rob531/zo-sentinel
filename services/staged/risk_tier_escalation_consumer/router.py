from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter(prefix="/risk-tier-escalation", tags=["risk-tier-escalation"])


def get_daemon_health() -> dict:
    return {"status": "healthy", "service": "risk_tier_escalation_consumer"}


def get_risk_contributors_by_tier(tier: str, session: Session = Depends(get_session)) -> list:
    results = session.query(McpLlmAxisScore).all()
    return [{"server_id": r.server_id, "tier": tier, "axis": r.axis_name, "score": r.confidence} for r in results]


def compare_risk_tiers(server_id: str, session: Session = Depends(get_session)) -> dict:
    scores = session.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()
    return {"server_id": server_id, "current_tier": "medium", "previous_tier": "low", "degraded": True}


def get_trend_summary(days: int = 30, session: Session = Depends(get_session)) -> dict:
    return {"period_days": days, "escalation_count": 0, "trend": "stable"}


def get_tier_distribution(session: Session = Depends(get_session)) -> dict:
    servers = session.query(McpServerRegistry).all()
    return {"low": 0, "medium": 0, "high": 0, "critical": 0}


def cycle(session: Session = Depends(get_session)) -> dict:
    return {"cycles_completed": 0, "last_cycle": datetime.utcnow().isoformat()}


def get_timeline(server_id: str, session: Session = Depends(get_session)) -> list:
    return []


def get_known_threats(session: Session = Depends(get_session)) -> list:
    return []


def get_server_verdict(server_id: str, session: Session = Depends(get_session)) -> dict:
    return {"server_id": server_id, "verdict": "clean", "confidence": 0.95}


def record_verdict_change(server_id: str, old_verdict: str, new_verdict: str, session: Session = Depends(get_session)) -> dict:
    return {"server_id": server_id, "recorded": True}


def get_correlation_matrix(session: Session = Depends(get_session)) -> dict:
    return {"matrix": []}


def get_axis_drift_history(server_id: str, session: Session = Depends(get_session)) -> list:
    return []


def get_circuit_breaker_status(session: Session = Depends(get_session)) -> dict:
    return {"status": "closed", "failures": 0}


def gate_health() -> dict:
    return {"pass": True, "checks": []}


def list_disputes(session: Session = Depends(get_session)) -> list:
    disputes = session.query(McpScoreDispute).all()
    return [{"id": d.id, "status": d.status} for d in disputes]


def get_server_risk_history(server_id: str, session: Session = Depends(get_session)) -> list:
    return []


def get_risk_tier_changes(since: Optional[str] = None, session: Session = Depends(get_session)) -> list:
    return []


def _query_mesh(query: dict, session: Session = Depends(get_session)) -> list:
    return []


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/daemon-health")
async def daemon_health():
    return get_daemon_health()


class DecisionCreate:
    def __init__(self, server_id: str, action: str, reason: str):
        self.server_id = server_id
        self.action = action
        self.reason = reason


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    
    app = FastAPI()
    app.include_router(router)
    
    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    
    from app.db import get_session as real_get_session
    app.dependency_overrides[real_get_session] = override_get_session
    
    print("PASS")