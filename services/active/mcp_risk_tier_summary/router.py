# deps: fastapi, pydantic, sqlalchemy
"""router.py -- HTTP surface for mcp_risk_tier_summary_view.

Provides aggregated risk tier summary across all servers with breakdown by axis.
Reads from app Postgres: McpServerRegistry, McpLlmAxisScore.
Public endpoint (auth=public per the directive).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["mcp_risk_tier_summary_view"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class AxisScoreSummary(BaseModel):
    axis_name: str
    avg_p_top: float | None
    avg_p_critical: float | None
    avg_p_danger: float | None
    servers_count: int


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int


class VerdictTierCount(BaseModel):
    verdict: str
    count: int


class RiskTierSummaryResponse(BaseModel):
    total_servers: int
    scored_servers: int
    risk_tier_distribution: list[RiskTierCount]
    verdict_distribution: list[VerdictTierCount]
    axis_summaries: list[AxisScoreSummary]
    criteria_version: str
    as_of: str


# ---------------------------------------------------------------------------
# Core data access
# ---------------------------------------------------------------------------

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


def compute_composite_score(p_top: float | None) -> float:
    return round(p_top if p_top is not None else 0.0, 4)


def map_to_risk_tier(composite_score: float) -> str:
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if composite_score >= threshold:
            return tier
    return "INSUFFICIENT"


def get_risk_tier_summary(db: Session) -> RiskTierSummaryResponse:
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Risk tier distribution
    risk_tier_rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    risk_tier_distribution = [
        RiskTierCount(risk_tier=(rt or "UNKNOWN"), count=cnt)
        for rt, cnt in risk_tier_rows
    ]

    # Verdict distribution
    verdict_rows = (
        db.query(
            McpServerRegistry.verdict,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.verdict)
        .all()
    )
    verdict_distribution = [
        VerdictTierCount(verdict=(v or "UNKNOWN"), count=cnt)
        for v, cnt in verdict_rows
    ]

    # Axis summaries (latest score per server)
    scored_servers = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .all()
    )
    scored_servers_count = len(scored_servers)

    # Get axis-level aggregates
    axis_agg = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("servers_count"),
        )
        .group_by(McpLlmAxisScore.axis_name)
        .all()
    )

    axis_summaries = [
        AxisScoreSummary(
            axis_name=row.axis_name,
            avg_p_top=round(float(row.avg_p_top), 4) if row.avg_p_top else None,
            avg_p_critical=round(float(row.avg_p_critical), 4) if row.avg_p_critical else None,
            avg_p_danger=round(float(row.avg_p_danger), 4) if row.avg_p_danger else None,
            servers_count=row.servers_count,
        )
        for row in axis_agg
    ]

    return RiskTierSummaryResponse(
        total_servers=total_servers,
        scored_servers=scored_servers_count,
        risk_tier_distribution=risk_tier_distribution,
        verdict_distribution=verdict_distribution,
        axis_summaries=axis_summaries,
        criteria_version="v1",
        as_of=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/views/risk_tier_summary", response_model=RiskTierSummaryResponse)
def risk_tier_summary_endpoint(
    db: Session = Depends(get_session),
) -> RiskTierSummaryResponse:
    """Return aggregated risk tier summary across all servers."""
    return get_risk_tier_summary(db)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
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

    # Seed test data
    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(
                server_id="srv-1",
                name="Trusted Server",
                risk_tier="TRUSTED_GENERAL",
                verdict="safe",
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="Risky Server",
                risk_tier="HIGH_RISK_ISOLATED",
                verdict="dangerous",
            ),
            McpServerRegistry(
                server_id="srv-3",
                name="Unknown Server",
                risk_tier=None,
                verdict=None,
            ),
        ]
        db.add_all(servers)
        db.flush()

        now = datetime.now(timezone.utc)

        # Add axis scores for srv-1 (high trust)
        for axis_name in ["overall_risk", "auth_strength", "capability_breadth",
                          "data_sensitivity", "network_egress", "maintainer_trust",
                          "exploit_surface"]:
            db.add(McpLlmAxisScore(
                server_id="srv-1",
                axis_name=axis_name,
                p_top=0.92,
                p_critical=0.05,
                p_danger=0.03,
                scored_at=now,
            ))

        # Add axis scores for srv-2 (high risk)
        for axis_name in ["overall_risk", "auth_strength", "capability_breadth",
                          "data_sensitivity", "network_egress", "maintainer_trust",
                          "exploit_surface"]:
            db.add(McpLlmAxisScore(
                server_id="srv-2",
                axis_name=axis_name,
                p_top=0.15,
                p_critical=0.35,
                p_danger=0.50,
                scored_at=now,
            ))

        db.commit()

    client = TestClient(app)

    # Test endpoint
    resp = client.get("/api/views/risk_tier_summary")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] == 3, f"total_servers mismatch: {data['total_servers']}"
    assert data["scored_servers"] == 2, f"scored_servers mismatch: {data['scored_servers']}"
    assert len(data["axis_summaries"]) >= 7, f"Expected 7+ axes, got {len(data['axis_summaries'])}"
    assert data["criteria_version"] == "v1"

    # Check axis names
    axis_names = {a["axis_name"] for a in data["axis_summaries"]}
    expected_axes = {
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"
    }
    assert expected_axes.issubset(axis_names), f"Missing axes: {expected_axes - axis_names}"

    print("PASS")
    sys.exit(0)
