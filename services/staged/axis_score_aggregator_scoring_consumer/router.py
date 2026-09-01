# services/staged/axis_score_aggregator_scoring_consumer/router.py
from fastapi import APIRouter, Depends
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
import requests

router = APIRouter()

AXIS_NAMES = [
    "dependency_confidence",
    "api_surface_compliance", 
    "security_headers",
    "vulnerability_exposure",
    "deployment_model",
    "secret_rotation",
    "network_posture"
]

TIER_MULTIPLIERS = {
    "critical": 1.3,
    "high": 1.2,
    "medium": 1.1,
    "low": 1.0
}

_last_processed_at = None
_servers_processed = 0


def get_composite_score(session: Session, server_id: str) -> float:
    if not session.execute(
        select(func.count(McpLlmAxisScore.id))
        .where(McpLlmAxisScore.server_id == server_id)
    ).scalar():
        return 0.0
    
    axis_scores = []
    evidence = {}
    
    for axis in AXIS_NAMES:
        result = session.execute(
            select(
                func.avg(McpLlmAxisScore.p_top).label('p_top'),
                func.avg(McpLlmAxisScore.p_critical).label('p_critical'),
                func.avg(McpLlmAxisScore.p_danger).label('p_danger')
            ).where(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name == axis
            )
        ).fetchone()
        
        if result:
            p_top = float(result.p_top) if result.p_top else 0.0
            p_critical = float(result.p_critical) if result.p_critical else 0.0
            p_danger = float(result.p_danger) if result.p_danger else 0.0
            axis_scores.append(p_top)
            evidence[axis] = {"p_top": p_top, "p_critical": p_critical, "p_danger": p_danger}
    
    if not axis_scores:
        return 0.0
    
    raw_score = sum(axis_scores) / len(axis_scores)
    server_row = session.execute(
        select(McpServerRegistry.risk_tier).where(McpServerRegistry.server_id == server_id)
    ).fetchone()
    
    multiplier = 1.0
    if server_row and server_row.risk_tier:
        multiplier = TIER_MULTIPLIERS.get(server_row.risk_tier.lower(), 1.0)
    
    return min(100.0, raw_score * 100 * multiplier)


def post_signal(server_id: str, composite_score: float, evidence_blob: dict):
    payload = {
        "server_id": server_id,
        "signal_type": "axis_aggregator_composite",
        "confidence": 0.85,
        "composite_score": composite_score,
        "evidence_blob": {"axis_scores": evidence_blob}
    }
    try:
        requests.post("http://127.0.0.1:8772/query", json=payload, timeout=5)
    except Exception:
        pass


@router.get("/health")
def health():
    return {"status": "healthy", "last_processed_at": _last_processed_at, "servers_count": _servers_processed}


@router.post("/process")
def process_scores(session: Session = Depends(get_session)):
    global _last_processed_at, _servers_processed
    
    server_ids = session.execute(
        select(func.distinct(McpLlmAxisScore.server_id)).where(
            McpLlmAxisScore.axis_name.in_(AXIS_NAMES)
        )
    ).scalars().all()
    
    _servers_processed = len(server_ids)
    
    for server_id in server_ids:
        score = get_composite_score(session, server_id)
        evidence = {}
        for axis in AXIS_NAMES:
            result = session.execute(
                select(
                    func.avg(McpLlmAxisScore.p_top).label('p_top'),
                    func.avg(McpLlmAxisScore.p_critical).label('p_critical'),
                    func.avg(McpLlmAxisScore.p_danger).label('p_danger')
                ).where(
                    McpLlmAxisScore.server_id == server_id,
                    McpLlmAxisScore.axis_name == axis
                )
            ).fetchone()
            if result:
                evidence[axis] = {
                    "p_top": float(result.p_top) if result.p_top else 0.0,
                    "p_critical": float(result.p_critical) if result.p_critical else 0.0,
                    "p_danger": float(result.p_danger) if result.p_danger else 0.0
                }
        post_signal(server_id, score, evidence)
    
    from datetime import datetime
    _last_processed_at = datetime.utcnow().isoformat()
    return {"status": "processed", "servers": _servers_processed}


if __name__ == "__main__":
    from fastapi import FastAPI
    from app.models import Base
    
    captured_response = {}
    
    def mock_post(url, json=None, timeout=None):
        captured_response[json["server_id"]] = json
        class Resp:
            status_code = 200
        return Resp()
    
    requests.post = mock_post
    
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    app = FastAPI()
    app.include_router(router)
    
    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    with TestingSessionLocal() as session:
        session.add(McpServerRegistry(server_id="srv-001", name="Low Risk Server", risk_tier="low", url="http://low.example.com"))
        session.add(McpServerRegistry(server_id="srv-002", name="Medium Risk Server", risk_tier="medium", url="http://med.example.com"))
        session.add(McpServerRegistry(server_id="srv-003", name="Critical Risk Server", risk_tier="critical", url="http://crit.example.com"))
        
        for i, axis in enumerate(AXIS_NAMES[:7]):
            session.add(McpLlmAxisScore(server_id="srv-001", axis_name=axis, label_index=0, p_top=0.6, p_critical=0.3, p_danger=0.1, label="stable", model_version="v1", adapter_sha256="abc", decision_rule_version="v1", scored_at="2024-01-01"))
            session.add(McpLlmAxisScore(server_id="srv-002", axis_name=axis, label_index=0, p_top=0.7, p_critical=0.2, p_danger=0.1, label="stable", model_version="v1", adapter_sha256="abc", decision_rule_version="v1", scored_at="2024-01-01"))
            session.add(McpLlmAxisScore(server_id="srv-003", axis_name=axis, label_index=0, p_top=0.8, p_critical=0.15, p_danger=0.05, label="stable", model_version="v1", adapter_sha256="abc", decision_rule_version="v1", scored_at="2024-01-01"))
        session.commit()
    
    with FastAPI().__with_app__(app):
        from fastapi.testclient import TestClient
        client = TestClient(app)
        client.post("/process")
    
    assert len(captured_response) == 3, f"Expected 3 signals, got {len(captured_response)}"
    assert "srv-001" in captured_response
    assert "srv-002" in captured_response
    assert "srv-003" in captured_response
    
    for sid, sig in captured_response.items():
        assert sig["signal_type"] == "axis_aggregator_composite"
        assert 0 <= sig["composite_score"] <= 100, f"Score out of range for {sid}: {sig['composite_score']}"
    
    print("PASS")