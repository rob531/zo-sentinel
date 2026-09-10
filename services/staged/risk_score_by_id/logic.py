# services/staged/risk_score_by_id/logic.py
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpLlmAxisScore

router = APIRouter()


class AxisInfo(BaseModel):
    label: str
    p_top: float


class RiskScoreResponse(BaseModel):
    axes: Dict[str, AxisInfo]
    overall_risk: float
    risk_tier: str


@router.get(
    "/servers/{server_id}/risk-scores",
    response_model=RiskScoreResponse,
    name="risk_score_by_id:get_risk_scores",
)
def get_risk_scores(
    server_id: int, db: Session = Depends(get_session)
) -> RiskScoreResponse:
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")

    axes: Dict[str, AxisInfo] = {}
    overall_risk = 0.0
    critical_override = False

    for row in rows:
        axes[row.axis_name] = AxisInfo(label=row.label, p_top=row.p_top)
        overall_risk = max(overall_risk, row.p_top)
        if getattr(row, "p_critical", 0) and row.p_critical > 0.5:
            critical_override = True

    if critical_override:
        tier = "CRITICAL"
    else:
        if overall_risk >= 0.75:
            tier = "HIGH"
        elif overall_risk >= 0.5:
            tier = "MEDIUM"
        else:
            tier = "LOW"

    return RiskScoreResponse(
        axes=axes, overall_risk=overall_risk, risk_tier=tier
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    import datetime

    # ------------------------------------------------------------------- #
    # In‑memory DB setup (overrides the real app DB)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # ------------------------------------------------------------------- #
    # Seed test data: 7 axes, one of them critical enough to force tier
    # ------------------------------------------------------------------- #
    db = TestSessionLocal()
    server_id = 1
    axis_names = [
        "confidentiality",
        "integrity",
        "availability",
        "authenticity",
        "nonrepudiation",
        "privacy",
        "resilience",
    ]
    for idx, axis in enumerate(axis_names):
        score = McpLlmAxisScore(
            id=idx + 1,
            server_id=server_id,
            axis_name=axis,
            label=f"Label {axis}",
            label_index=idx,
            adapter_sha256="dummyhash",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            model_version="model-1",
            p_top=0.4 + idx * 0.05,
            p_critical=0.6 if axis == "integrity" else 0.1,
            p_danger=0.2,
            probs={},
            scored_at=datetime.datetime.utcnow(),
        )
        db.add(score)
    db.commit()
    db.close()

    # ------------------------------------------------------------------- #
    # Run test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get(f"/servers/{server_id}/risk-scores")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert isinstance(payload, dict)
    assert "axes" in payload and isinstance(payload["axes"], dict)
    assert len(payload["axes"]) == 7, "Expected 7 axes"
    assert payload["risk_tier"] == "CRITICAL", "Critical override not applied"
    print("PASS")