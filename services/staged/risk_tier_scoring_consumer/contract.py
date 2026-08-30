from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()


class AxisScoreInput(BaseModel):
    server_id: str
    axis_name: str
    label: str
    label_index: int
    probs: List[float]
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    p_top: Optional[float] = None
    model_version: str
    decision_rule_version: str
    adapter_sha256: str
    scored_at: str


class RiskTierVerdict(BaseModel):
    server_id: str
    risk_tier: str
    confidence: float
    verdict_reasoning: str


class RiskTierResponse(BaseModel):
    server_id: str
    risk_tier: Optional[str] = None
    confidence: Optional[float] = None
    verdict: Optional[str] = None
    verdict_reasoning: Optional[str] = None


def compute_risk_tier(label: str, label_index: int, p_critical: Optional[float] = None) -> tuple[str, float]:
    tier_map = {
        "critical": ("CRITICAL", 0.95),
        "high": ("HIGH", 0.85),
        "medium": ("MEDIUM", 0.70),
        "low": ("LOW", 0.55),
        "minimal": ("MINIMAL", 0.40),
    }
    tier, base_confidence = tier_map.get(label.lower(), ("UNKNOWN", 0.30))
    if p_critical is not None and p_critical > 0.7:
        tier = "CRITICAL"
        base_confidence = 0.95
    return tier, base_confidence


@router.post("/process_scores", response_model=RiskTierVerdict)
def process_scores(
    scores: List[AxisScoreInput],
    db: Session = Depends(get_session),
):
    verdicts = []
    for score in scores:
        risk_tier, confidence = compute_risk_tier(score.label, score.label_index, score.p_critical)
        reasoning = f"Axis '{score.axis_name}' scored as '{score.label}' (index={score.label_index})"
        
        db.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == score.server_id
        ).update({
            "risk_tier": risk_tier,
            "confidence": confidence,
            "verdict": risk_tier,
            "verdict_reasoning": reasoning,
        })
        
        verdicts.append(RiskTierVerdict(
            server_id=score.server_id,
            risk_tier=risk_tier,
            confidence=confidence,
            verdict_reasoning=reasoning,
        ))
    
    db.commit()
    return verdicts


@router.post("/score_received", response_model=dict)
def score_received(
    score: AxisScoreInput,
    db: Session = Depends(get_session),
):
    risk_tier, confidence = compute_risk_tier(score.label, score.label_index, score.p_critical)
    reasoning = f"Received axis '{score.axis_name}' score for server"
    
    db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == score.server_id
    ).update({
        "risk_tier": risk_tier,
        "confidence": confidence,
        "verdict": risk_tier,
        "verdict_reasoning": reasoning,
    })
    
    db.commit()
    
    return {
        "status": "processed",
        "server_id": score.server_id,
        "risk_tier": risk_tier,
        "confidence": confidence,
    }


@router.get("/risk_tier/{server_id}", response_model=RiskTierResponse)
def get_risk_tier(server_id: str, db: Session = Depends(get_session)):
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    
    if not server:
        return RiskTierResponse(server_id=server_id)
    
    return RiskTierResponse(
        server_id=server.server_id,
        risk_tier=server.risk_tier,
        confidence=server.confidence,
        verdict=server.verdict,
        verdict_reasoning=server.verdict_reasoning,
    )


@router.get("/risk_tiers", response_model=List[RiskTierResponse])
def get_all_risk_tiers(db: Session = Depends(get_session)):
    servers = db.query(McpServerRegistry).all()
    return [
        RiskTierResponse(
            server_id=s.server_id,
            risk_tier=s.risk_tier,
            confidence=s.confidence,
            verdict=s.verdict,
            verdict_reasoning=s.verdict_reasoning,
        )
        for s in servers
    ]


@router.get("/health")
def health():
    return {"status": "healthy", "service": "risk_tier_scoring_consumer"}


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    session = TestingSessionLocal()
    test_server = McpServerRegistry(
        server_id="srv_test_001",
        name="Test Server",
        url="https://test.example.com",
        registry_source="test",
        risk_tier=None,
        confidence=None,
    )
    session.add(test_server)
    session.commit()
    session.close()

    client = TestClient(test_app)

    response = client.get("/health")
    assert response.status_code == 200, f"Health check failed: {response.text}"

    score_payload = {
        "server_id": "srv_test_001",
        "axis_name": "security",
        "label": "high",
        "label_index": 2,
        "probs": [0.1, 0.2, 0.5, 0.2],
        "model_version": "v1.0",
        "decision_rule_version": "r1",
        "adapter_sha256": "abc123",
        "scored_at": "2024-01-01T00:00:00Z",
    }

    response = client.post("/score_received", json=score_payload)
    assert response.status_code == 200, f"Score processing failed: {response.text}"
    data = response.json()
    assert data["risk_tier"] == "HIGH", f"Expected HIGH, got {data.get('risk_tier')}"

    response = client.get("/risk_tier/srv_test_001")
    assert response.status_code == 200, f"Risk tier query failed: {response.text}"
    data = response.json()
    assert data["risk_tier"] == "HIGH", f"Expected HIGH, got {data.get('risk_tier')}"
    assert data["confidence"] is not None

    print("PASS")
    sys.exit(0)