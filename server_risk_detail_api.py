from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

router = APIRouter()


class AxisDetail(BaseModel):
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    probs: List[float] = Field(..., alias="probs")
    escalated: bool


class ServerRiskDetailResponse(BaseModel):
    server_id: int
    axes: Dict[str, AxisDetail]
    composite_score: float
    risk_tier: str
    criteria_version: str | None = None
    scored_at: datetime | None = None


AXIS_NAMES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


def _compute_risk_tier(composite: float, escalated: bool) -> str:
    if escalated:
        return "HIGH_RISK_ISOLED"
    if composite > 75:
        return "TRUSTED_GENERAL"
    if composite > 60:
        return "TRUSTED_RESEARCH"
    if composite > 45:
        return "ENTERPRISE_CONTROLLED"
    if composite > 30:
        return "CAUTION_LIMITED"
    if composite > 15:
        return "HIGH_RISK_ISOLED"
    return "KNOWN_THREAT"


@router.get(
    "/servers/{server_id}/risk_detail",
    response_model=ServerRiskDetailResponse,
    name="Get server risk detail",
)
def get_risk_detail(
    server_id: int, db: Session = Depends(get_session)
) -> ServerRiskDetailResponse:
    scores = (
        db.query(McpLlmAxisScores).filter(McpLlmAxisScores.server_id == server_id).first()
    )
    if not scores:
        raise HTTPException(status_code=404, detail="Server not found")

    axes: Dict[str, AxisDetail] = {}
    any_escalated = False

    for name in AXIS_NAMES:
        label = getattr(scores, f"{name}_label")
        label_index = getattr(scores, f"{name}_label_index")
        p_top = getattr(scores, f"{name}_p_top")
        p_critical = getattr(scores, f"{name}_p_critical")
        p_danger = getattr(scores, f"{name}_p_danger")
        escalated = label.upper() == "CRITICAL"
        any_escalated = any_escalated or escalated
        axes[name] = AxisDetail(
            label=label,
            label_index=label_index,
            p_top=p_top,
            p_critical=p_critical,
            p_danger=p_danger,
            probs=[p_top, p_critical, p_danger],
            escalated=escalated,
        )

    # composite score from the six non‑overall axes
    composite_score = sum(
        getattr(scores, f"{axis}_p_top")
        + getattr(scores, f"{axis}_p_critical")
        + getattr(scores, f"{axis}_p_danger")
        for axis in AXIS_NAMES[1:]
    )

    risk_tier = _compute_risk_tier(composite_score, any_escalated)

    return ServerRiskDetailResponse(
        server_id=server_id,
        axes=axes,
        composite_score=composite_score,
        risk_tier=risk_tier,
        criteria_version=getattr(scores, "criteria_version", None),
        scored_at=getattr(scores, "scored_at", None),
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create tables using the same Base as the application models
    from app.models import Base

    Base.metadata.create_all(bind=engine)

    # Seed data
    db: Session = SessionLocal()
    # three servers
    db.add_all([McpServerRegistry(server_id=1), McpServerRegistry(server_id=2), McpServerRegistry(server_id=3)])

    def make_score(sid: int, composite: float) -> McpLlmAxisScores:
        # distribute composite equally across the six axes via p_top only
        p_top = composite / 6.0
        return McpLlmAxisScores(
            server_id=sid,
            overall_risk_label="LOW",
            overall_risk_label_index=0,
            overall_risk_p_top=0.0,
            overall_risk_p_critical=0.0,
            overall_risk_p_danger=0.0,
            auth_strength_label="LOW",
            auth_strength_label_index=0,
            auth_strength_p_top=p_top,
            auth_strength_p_critical=0.0,
            auth_strength_p_danger=0.0,
            capability_breadth_label="LOW",
            capability_breadth_label_index=0,
            capability_breadth_p_top=p_top,
            capability_breadth_p_critical=0.0,
            capability_breadth_p_danger=0.0,
            data_sensitivity_label="LOW",
            data_sensitivity_label_index=0,
            data_sensitivity_p_top=p_top,
            data_sensitivity_p_critical=0.0,
            data_sensitivity_p_danger=0.0,
            network_egress_label="LOW",
            network_egress_label_index=0,
            network_egress_p_top=p_top,
            network_egress_p_critical=0.0,
            network_egress_p_danger=0.0,
            maintainer_trust_label="LOW",
            maintainer_trust_label_index=0,
            maintainer_trust_p_top=p_top,
            maintainer_trust_p_critical=0.0,
            maintainer_trust_p_danger=0.0,
            exploit_surface_label="LOW",
            exploit_surface_label_index=0,
            exploit_surface_p_top=p_top,
            exploit_surface_p_critical=0.0,
            exploit_surface_p_danger=0.0,
            criteria_version="v1",
            scored_at=datetime.utcnow(),
        )

    db.add_all(
        [
            make_score(1, 80.0),
            make_score(2, 45.0),
            make_score(3, 10.0),
        ]
    )
    db.commit()
    db.close()

    # FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)


    def get_test_session() -> Session:
        return SessionLocal()


    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    test_cases = [
        (1, 80.0, "TRUSTED_GENERAL"),
        (2, 45.0, "CAUTION_LIMITED"),
        (3, 10.0, "KNOWN_THREAT"),
    ]

    for sid, expected_comp, expected_tier in test_cases:
        resp = client.get(f"/servers/{sid}/risk_detail")
        assert resp.status_code == 200, f"Server {sid} returned {resp.status_code}"
        data = resp.json()
        assert abs(data["composite_score"] - expected_comp) < 0.01
        assert data["risk_tier"] == expected_tier

    print("PASS")