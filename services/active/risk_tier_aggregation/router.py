# deps: fastapi, pydantic, sqlalchemy
"""router.py -- HTTP surface for risk_tier_aggregation."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_aggregation"])


class AxisBreakdown(BaseModel):
    axis_name: str
    p_top: float
    p_critical: float
    p_danger: float
    label: str


class RiskTierResponse(BaseModel):
    server_id: str
    server_name: str | None
    composite_score: float
    risk_tier: str
    axis_breakdown: list[AxisBreakdown]
    criteria_version: str
    scored_at: str


AXIS_WEIGHTS = {
    "overall_risk": 0.25,
    "auth_strength": 0.12,
    "capability_breadth": 0.10,
    "data_sensitivity": 0.18,
    "network_egress": 0.15,
    "maintainer_trust": 0.12,
    "exploit_surface": 0.08,
}

RISK_TIER_THRESHOLDS = [
    (0.90, "TRUSTED_GENERAL"),
    (0.75, "TRUSTED_RESEARCH"),
    (0.60, "ENTERPRISE_CONTROLLED"),
    (0.40, "CAUTION_LIMITED"),
    (0.20, "HIGH_RISK_ISOLATED"),
    (0.00, "INSUFFICIENT"),
]


def compute_composite_score(axis_rows: list[dict[str, Any]]) -> tuple[float, list[AxisBreakdown]]:
    if not axis_rows:
        return 0.0, []

    breakdown = []
    weighted_sum = 0.0
    total_weight = 0.0

    for row in axis_rows:
        axis_name = row["axis_name"]
        p_top = row["p_top"] or 0.0
        p_critical = row["p_critical"] or 0.0
        p_danger = row["p_danger"] or 0.0

        if p_top >= 0.7:
            label = "excellent"
        elif p_top >= 0.5:
            label = "good"
        elif p_top >= 0.3:
            label = "moderate"
        elif p_top >= 0.1:
            label = "concerning"
        else:
            label = "critical"

        breakdown.append(AxisBreakdown(
            axis_name=axis_name,
            p_top=round(p_top, 4),
            p_critical=round(p_critical, 4),
            p_danger=round(p_danger, 4),
            label=label,
        ))

        weight = AXIS_WEIGHTS.get(axis_name, 0.1)
        weighted_sum += p_top * weight
        total_weight += weight

    composite = weighted_sum / total_weight if total_weight > 0 else 0.0
    return round(composite, 4), breakdown


def map_to_risk_tier(composite_score: float) -> str:
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if composite_score >= threshold:
            return tier
    return "INSUFFICIENT"


def get_risk_tier_aggregation(db: Session, server_id: str) -> RiskTierResponse | None:
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        return None

    server_name = server.name

    axis_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).order_by(McpLlmAxisScore.scored_at.desc()).limit(7).all()

    if not axis_scores:
        return RiskTierResponse(
            server_id=server_id,
            server_name=server_name,
            composite_score=0.0,
            risk_tier="INSUFFICIENT",
            axis_breakdown=[],
            criteria_version="v1",
            scored_at=datetime.now(timezone.utc).isoformat(),
        )

    axis_data = [
        {
            "axis_name": s.axis_name,
            "p_top": s.p_top,
            "p_critical": s.p_critical,
            "p_danger": s.p_danger,
        }
        for s in axis_scores
    ]

    latest_scored_at = axis_scores[0].scored_at
    scored_at_str = (
        latest_scored_at.isoformat()
        if isinstance(latest_scored_at, datetime)
        else str(latest_scored_at)
    )

    composite_score, axis_breakdown = compute_composite_score(axis_data)
    risk_tier = map_to_risk_tier(composite_score)

    return RiskTierResponse(
        server_id=server_id,
        server_name=server_name,
        composite_score=composite_score,
        risk_tier=risk_tier,
        axis_breakdown=axis_breakdown,
        criteria_version="v1",
        scored_at=scored_at_str,
    )


@router.get("/scoring/risk-tier", response_model=RiskTierResponse)
def risk_tier_endpoint(
    server_id: str = Query(..., description="MCP server identifier"),
    db: Session = Depends(get_session),
) -> RiskTierResponse:
    result = get_risk_tier_aggregation(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return result


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestSessionLocal() as db:
        s1 = McpServerRegistry(server_id="trusted-srv", name="Trusted Server", risk_tier="low")
        s2 = McpServerRegistry(server_id="risky-srv", name="Risky Server", risk_tier="high")
        s3 = McpServerRegistry(server_id="unknown-srv", name="Unknown Server", risk_tier=None)
        db.add_all([s1, s2, s3])
        db.flush()

        now = datetime.now(timezone.utc)

        high_axes = [
            ("overall_risk", 0.95, 0.03, 0.02),
            ("auth_strength", 0.90, 0.05, 0.05),
            ("capability_breadth", 0.85, 0.08, 0.07),
            ("data_sensitivity", 0.92, 0.04, 0.04),
            ("network_egress", 0.88, 0.06, 0.06),
            ("maintainer_trust", 0.94, 0.03, 0.03),
            ("exploit_surface", 0.91, 0.05, 0.04),
        ]
        for axis_name, p_top, p_critical, p_danger in high_axes:
            db.add(McpLlmAxisScore(
                server_id="trusted-srv",
                axis_name=axis_name,
                p_top=p_top,
                p_critical=p_critical,
                p_danger=p_danger,
                scored_at=now,
            ))

        low_axes = [
            ("overall_risk", 0.15, 0.30, 0.55),
            ("auth_strength", 0.20, 0.25, 0.55),
            ("capability_breadth", 0.25, 0.25, 0.50),
            ("data_sensitivity", 0.18, 0.32, 0.50),
            ("network_egress", 0.22, 0.28, 0.50),
            ("maintainer_trust", 0.12, 0.28, 0.60),
            ("exploit_surface", 0.10, 0.30, 0.60),
        ]
        for axis_name, p_top, p_critical, p_danger in low_axes:
            db.add(McpLlmAxisScore(
                server_id="risky-srv",
                axis_name=axis_name,
                p_top=p_top,
                p_critical=p_critical,
                p_danger=p_danger,
                scored_at=now,
            ))

        db.commit()

    client = TestClient(app)

    resp1 = client.get("/api/scoring/risk-tier", params={"server_id": "trusted-srv"})
    assert resp1.status_code == 200, f"trusted-srv failed: {resp1.status_code}"
    data1 = resp1.json()
    assert data1["risk_tier"] == "TRUSTED_GENERAL", f"Expected TRUSTED_GENERAL, got {data1['risk_tier']}"
    assert data1["server_id"] == "trusted-srv"
    assert len(data1["axis_breakdown"]) == 7

    resp2 = client.get("/api/scoring/risk-tier", params={"server_id": "risky-srv"})
    assert resp2.status_code == 200, f"risky-srv failed: {resp2.status_code}"
    data2 = resp2.json()
    assert data2["risk_tier"] == "HIGH_RISK_ISOLATED", f"Expected HIGH_RISK_ISOLATED, got {data2['risk_tier']}"
    assert data2["server_id"] == "risky-srv"

    resp3 = client.get("/api/scoring/risk-tier", params={"server_id": "unknown-srv"})
    assert resp3.status_code == 200, f"unknown-srv failed: {resp3.status_code}"
    data3 = resp3.json()
    assert data3["risk_tier"] == "INSUFFICIENT", f"Expected INSUFFICIENT, got {data3['risk_tier']}"

    resp4 = client.get("/api/scoring/risk-tier", params={"server_id": "nonexistent"})
    assert resp4.status_code == 404, f"nonexistent should return 404, got {resp4.status_code}"

    print("PASS")
    sys.exit(0)
