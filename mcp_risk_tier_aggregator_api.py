from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base
from sqlalchemy.pool import StaticPool
from datetime import datetime
from app.db import get_session
from app.models import Base

router = APIRouter(prefix="/servers", tags=["risk-tier"])

AXIS_NAMES = ["auth_strength", "capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]

TIER_THRESHOLDS = [
    (75, "TRUSTED_GENERAL"),
    (60, "TRUSTED_RESEARCH"),
    (45, "ENTERPRISE_CONTROLLED"),
    (30, "CAUTION_LIMITED"),
    (15, "HIGH_RISK_ISOLATED"),
]


class AxisScoreResponse(BaseModel):
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class RiskTierResponse(BaseModel):
    server_id: str
    axes: dict[str, AxisScoreResponse]
    overall_risk_label: str
    overall_risk_p_top: float
    derived_tier: str
    criteria_version: str
    scored_at: Optional[str] = None


def derive_tier(axes: dict[str, dict], overall: Optional[dict]) -> str:
    has_critical = any(ax.get("label") == "CRITICAL" for ax in axes.values())
    if has_critical:
        return "KNOWN_THREAT"

    if overall:
        present = sum(1 for name in AXIS_NAMES if name in axes)
        if present < 5:
            return "INSUFFICIENT"

        composite = overall.get("p_top", 0)
        for threshold, tier in TIER_THRESHOLDS:
            if composite > threshold:
                return tier
        return "KNOWN_THREAT"

    return "INSUFFICIENT"


@router.get("/{server_id}/risk-tier", response_model=RiskTierResponse)
def get_risk_tier(server_id: str, session: Session = Depends(get_session)):
    from app.models import MCPLLMAxisScore

    rows = session.query(MCPLLMAxisScore).filter(MCPLLMAxisScore.server_id == server_id).all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scores found for server_id: {server_id}")

    axes_data = {}
    overall_data = None

    for row in rows:
        axis_name = row.axis_name
        label_index = row.label_index if row.label_index is not None else 0
        label = row.label if row.label else "UNKNOWN"

        axis_info = {
            "label": label,
            "label_index": label_index,
            "p_top": row.p_top if row.p_top is not None else 0.0,
            "p_critical": row.p_critical if row.p_critical is not None else 0.0,
            "p_danger": row.p_danger if row.p_danger is not None else 0.0,
            "escalated": row.escalated if row.escalated is not None else False,
        }

        if axis_name == "overall_risk":
            overall_data = axis_info
        else:
            axes_data[axis_name] = axis_info

    derived_tier = derive_tier(axes_data, overall_data)

    overall_label = overall_data["label"] if overall_data else "UNKNOWN"
    overall_p_top = overall_data["p_top"] if overall_data else 0.0
    scored_at = rows[0].scored_at.isoformat() if rows[0].scored_at else None

    return RiskTierResponse(
        server_id=server_id,
        axes=axes_data,
        overall_risk_label=overall_label,
        overall_risk_p_top=overall_p_top,
        derived_tier=derived_tier,
        criteria_version="1.0.0",
        scored_at=scored_at,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_session = TestingSessionLocal()

    from app.models import MCPLLMAxisScore

    test_server_id = "test-srv-001"
    test_rows = [
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="overall_risk",
            label="MEDIUM",
            label_index=2,
            p_top=0.65,
            p_critical=0.10,
            p_danger=0.25,
            escalated=False,
            scored_at=datetime.utcnow(),
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="auth_strength",
            label="STRONG",
            label_index=3,
            p_top=0.80,
            p_critical=0.05,
            p_danger=0.15,
            escalated=False,
            scored_at=datetime.utcnow(),
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="capability_breadth",
            label="MODERATE",
            label_index=2,
            p_top=0.50,
            p_critical=0.15,
            p_danger=0.35,
            escalated=False,
            scored_at=datetime.utcnow(),
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="data_sensitivity",
            label="LOW",
            label_index=1,
            p_top=0.70,
            p_critical=0.05,
            p_danger=0.25,
            escalated=False,
            scored_at=datetime.utcnow(),
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="network_egress",
            label="MINIMAL",
            label_index=1,
            p_top=0.75,
            p_critical=0.05,
            p_danger=0.20,
            escalated=False,
            scored_at=datetime.utcnow(),
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="maintainer_trust",
            label="HIGH",
            label_index=3,
            p_top=0.85,
            p_critical=0.02,
            p_danger=0.13,
            escalated=False,
            scored_at=datetime.utcnow(),
        ),
        MCPLLMAxisScore(
            server_id=test_server_id,
            axis_name="exploit_surface",
            label="LOW",
            label_index=1,
            p_top=0.60,
            p_critical=0.10,
            p_danger=0.30,
            escalated=False,
            scored_at=datetime.utcnow(),
        ),
    ]

    for row in test_rows:
        test_session.add(row)
    test_session.commit()

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.get(f"/servers/{test_server_id}/risk-tier")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert "derived_tier" in response.json(), "derived_tier not in response"

    print("PASS")