# deps: requests
import sys as _sys
from pathlib import Path as _Path

# Ensure repo root is on path so 'app' resolves when run directly.
_repo = _Path(__file__).resolve().parents[2]
if str(_repo) not in _sys.path:
    _sys.path.insert(0, str(_repo))

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import List

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["risk_recommendation_api"])


class RiskRecommendationResponse(BaseModel):
    server_id: str
    risk_tier: str
    action: str = Field(..., pattern="^(APPROVE|CONDITIONAL|REJECT|REVIEW)$")
    rationale: str
    flags: List[str] = Field(default_factory=list)
    guidance: str


@router.get("/risk/recommendation", response_model=RiskRecommendationResponse)
def get_risk_recommendation(
    server_id: str,
    session: Session = Depends(get_session),
):
    stmt = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    server = session.execute(stmt).scalar_one_or_none()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axis_stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    axis_scores = session.execute(axis_stmt).scalars().all()

    risk_tier = server.risk_tier
    flags = []

    if risk_tier in ("TRUSTED_GENERAL", "TRUSTED_RESEARCH"):
        action = "APPROVE"
    else:
        max_p_critical = 0.0
        for score in axis_scores:
            if score.p_critical is not None and score.p_critical > max_p_critical:
                max_p_critical = score.p_critical

        if max_p_critical > 0.6:
            action = "REJECT"
        else:
            summed_p_danger = 0.0
            for score in axis_scores:
                if score.p_danger is not None:
                    summed_p_danger += score.p_danger

            if summed_p_danger > 0.5:
                action = "CONDITIONAL"
            else:
                action = "REVIEW"

    rationale_parts = []
    if not axis_scores:
        rationale_parts.append("No axis scores available")
        flags.append("NO_AXIS_SCORES")
    rationale_parts.append(f"Risk tier: {risk_tier}")
    if action == "REJECT":
        rationale_parts.append(f"p_critical > 0.6 detected (max: {max_p_critical:.2f})")
        flags.append("CRITICAL_AXIS_EXCEEDED")
    elif action == "CONDITIONAL":
        rationale_parts.append(f"p_danger sum > 0.5 (sum: {summed_p_danger:.2f})")
        flags.append("ELEVATED_DANGER_SUM")
    rationale_parts.append(f"Recommendation: {action}")

    rationale = "; ".join(rationale_parts)

    guidance_map = {
        "APPROVE": "Proceed with standard monitoring and periodic reassessment.",
        "REJECT": "Immediately isolate and conduct thorough security review before any deployment.",
        "CONDITIONAL": "Deploy only in restricted contexts with enhanced monitoring and mitigation measures.",
        "REVIEW": "Conduct manual security review before deployment decision.",
    }
    guidance = guidance_map.get(action, "Consult security team for guidance.")

    return RiskRecommendationResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        action=action,
        rationale=rationale,
        flags=flags,
        guidance=guidance,
    )


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    session = TestSessionLocal()

    session.add(McpServerRegistry(
        server_id="trusted-server-001",
        name="Trusted General Server",
        url="https://trusted.example.com",
        registry_source="test",
        risk_tier="TRUSTED_GENERAL",
        trust_score=95.0,
        confidence=90.0,
        verdict="trusted",
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        last_scanned=datetime.now(),
        last_assessed=datetime.now(),
        meta={},
    ))
    session.add(McpServerRegistry(
        server_id="high-risk-server-001",
        name="High Risk Isolated Server",
        url="https://highrisk.example.com",
        registry_source="test",
        risk_tier="HIGH_RISK_ISOLATED",
        trust_score=20.0,
        confidence=85.0,
        verdict="high_risk",
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        last_scanned=datetime.now(),
        last_assessed=datetime.now(),
        meta={},
    ))
    session.add(McpServerRegistry(
        server_id="caution-server-001",
        name="Caution Limited Server",
        url="https://caution.example.com",
        registry_source="test",
        risk_tier="CAUTION_LIMITED",
        trust_score=50.0,
        confidence=80.0,
        verdict="caution",
        first_seen=datetime.now(),
        last_seen=datetime.now(),
        last_scanned=datetime.now(),
        last_assessed=datetime.now(),
        meta={},
    ))

    session.add(McpLlmAxisScore(
        server_id="trusted-server-001",
        axis_name="auth_strength",
        p_critical=0.05,
        p_danger=0.1,
        p_top=0.7,
        probs=[],
        label="strong",
        label_index=0,
        model_version="1.0",
        decision_rule_version="1.0",
        adapter_sha256="abc123",
        scored_at=datetime.now(),
        escalated=False,
        escalated_to=None,
    ))
    session.add(McpLlmAxisScore(
        server_id="high-risk-server-001",
        axis_name="auth_strength",
        p_critical=0.7,
        p_danger=0.2,
        p_top=0.1,
        probs=[],
        label="weak",
        label_index=3,
        model_version="1.0",
        decision_rule_version="1.0",
        adapter_sha256="abc123",
        scored_at=datetime.now(),
        escalated=False,
        escalated_to=None,
    ))
    session.add(McpLlmAxisScore(
        server_id="caution-server-001",
        axis_name="auth_strength",
        p_critical=0.7,
        p_danger=0.1,
        p_top=0.2,
        probs=[],
        label="weak",
        label_index=3,
        model_version="1.0",
        decision_rule_version="1.0",
        adapter_sha256="abc123",
        scored_at=datetime.now(),
        escalated=False,
        escalated_to=None,
    ))
    session.add(McpLlmAxisScore(
        server_id="caution-server-001",
        axis_name="data_exposure",
        p_critical=0.2,
        p_danger=0.3,
        p_top=0.5,
        probs=[],
        label="moderate",
        label_index=2,
        model_version="1.0",
        decision_rule_version="1.0",
        adapter_sha256="abc123",
        scored_at=datetime.now(),
        escalated=False,
        escalated_to=None,
    ))

    session.commit()
    session.close()

    response = client.get("/api/risk/recommendation?server_id=trusted-server-001")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["action"] == "APPROVE", f"trusted-server-001: expected APPROVE, got {data['action']}"

    response = client.get("/api/risk/recommendation?server_id=high-risk-server-001")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["action"] == "REJECT", f"high-risk-server-001: expected REJECT, got {data['action']}"

    response = client.get("/api/risk/recommendation?server_id=caution-server-001")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["action"] == "REJECT", f"caution-server-001: expected REJECT, got {data['action']}"

    print("PASS")
    _sys.exit(0)
