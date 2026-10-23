# services/staged/risk_summary_api/contract.py
"""
Risk Summary API contract.

Provides:
GET /api/servers/{server_id}/risk-summary
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, desc
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports (must not be stubbed)
from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


# ---------- Pydantic models ----------
class AxisInfo(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool


class RiskSummaryResponse(BaseModel):
    server_id: str
    server_name: str
    axes: List[AxisInfo] = Field(min_items=7)
    overall_risk: float
    risk_tier: str
    criteria_version: str
    scored_at: str


# ---------- Helper ----------
_RISK_TIER_MAP = [
    (15, "KNOWN_THREAT"),
    (30, "HIGH_RISK_ISOLATED"),
    (45, "CAUTION_LIMITED"),
    (60, "ENTERPRISE_CONTROLLED"),
    (float("inf"), "TRUSTED_GENERAL"),
]

_NEEDED_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


def _derive_risk_tier(overall_risk: float) -> str:
    for limit, tier in _RISK_TIER_MAP:
        if overall_risk <= limit:
            return tier
    return "UNKNOWN"


# ---------- Endpoint ----------
@router.get(
    "/servers/{server_id}/risk-summary",
    response_model=RiskSummaryResponse,
    name="risk_summary",
)
def get_risk_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> RiskSummaryResponse:
    # Server info
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Axis scores – newest per axis
    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(desc(McpLlmAxisScore.scored_at))
        .all()
    )
    latest_by_axis = {}
    for row in rows:
        if row.axis_name not in latest_by_axis:
            latest_by_axis[row.axis_name] = row
        if _NEEDED_AXES.issubset(latest_by_axis.keys()):
            break

    # Ensure we have at least the required axes
    missing = _NEEDED_AXES - latest_by_axis.keys()
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Missing axis scores: {', '.join(sorted(missing))}",
        )

    # Build axes list
    axes_list: List[AxisInfo] = []
    for axis_name in sorted(_NEEDED_AXES):
        score = latest_by_axis[axis_name]
        axes_list.append(
            AxisInfo(
                axis_name=axis_name,
                label=score.label,
                p_top=score.p_top,
                p_critical=score.p_critical,
                p_danger=score.p_danger,
                escalated=bool(score.escalated),
            )
        )

    overall_score = latest_by_axis["overall_risk"]
    overall_risk = overall_score.p_top
    risk_tier = _derive_risk_tier(overall_risk)

    return RiskSummaryResponse(
        server_id=server_id,
        server_name=server.name,
        axes=axes_list,
        overall_risk=overall_risk,
        risk_tier=risk_tier,
        criteria_version=overall_score.decision_rule_version,
        scored_at=overall_score.scored_at.isoformat(),
    )


# ---------- Self‑test ----------
if __name__ == "__main__":
    import sys

    # Build a test FastAPI app with an in‑memory SQLite DB
    test_app = FastAPI()
    test_app.include_router(router)

    # Create a test session that uses SQLite + StaticPool
    TEST_ENGINE = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=TEST_ENGINE)
    TestSessionLocal = sessionmaker(bind=TEST_ENGINE)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSessionLocal() as db:
        # Server entry
        server = McpServerRegistry(
            server_id="srv1",
            name="Test Server",
        )
        db.add(server)

        # Axis scores
        now = datetime.utcnow()
        axis_values = {
            "overall_risk": 20.0,
            "auth_strength": 10.0,
            "capability_breadth": 12.0,
            "data_sensitivity": 8.0,
            "network_egress": 15.0,
            "maintainer_trust": 9.0,
            "exploit_surface": 11.0,
        }
        for idx, (axis, p_top) in enumerate(axis_values.items(), start=1):
            score = McpLlmAxisScore(
                id=idx,
                server_id="srv1",
                axis_name=axis,
                adapter_sha256="dummy",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                label=f"{axis} label",
                label_index=0,
                model_version="1.0",
                p_critical=0.0,
                p_danger=0.0,
                p_top=p_top,
                probs="{}",
                scored_at=now,
            )
            db.add(score)

        db.commit()

    # Run test client
    from fastapi.testclient import TestClient

    client = TestClient(test_app)
    resp = client.get("/api/servers/srv1/risk-summary")
    try:
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert isinstance(data["risk_tier"], str)
        assert len(data["axes"]) >= 7
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)