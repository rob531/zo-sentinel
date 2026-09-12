# deps: fastapi, pydantic, sqlalchemy
"""router.py -- HTTP surface for risk_tier_registry_aggregator.

Aggregates risk tier data from the MCP server registry: distribution counts,
composite score summaries, and tier-by-source breakdowns.
Reads from app Postgres via SQLAlchemy session.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_registry_aggregator"])


# ---------------------------------------------------------------------------
# Constants (mirrors risk_tier_aggregation / mcp_risk_tier_summary_view)
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


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class TierCountItem(BaseModel):
    risk_tier: str
    count: int


class SourceTierItem(BaseModel):
    registry_source: str | None
    risk_tier: str
    count: int


class ServerAxisSummary(BaseModel):
    server_id: str
    server_name: str | None
    composite_score: float | None
    risk_tier: str
    axis_count: int
    last_scored_at: str | None


class RegistryAggregationResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    risk_tier_distribution: list[TierCountItem]
    source_tier_breakdown: list[SourceTierItem]
    unscored_servers_sample: list[ServerAxisSummary]
    as_of: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def map_to_risk_tier(composite_score: float | None) -> str:
    if composite_score is None:
        return "INSUFFICIENT"
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if composite_score >= threshold:
            return tier
    return "INSUFFICIENT"


def compute_composite_from_row(row: Any) -> float:
    p_top = row.avg_p_top
    if p_top is None:
        return 0.0
    total_weight = sum(AXIS_WEIGHTS.values())
    weighted_sum = 0.0
    for axis_name in [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"
    ]:
        weight = AXIS_WEIGHTS.get(axis_name, 0.1)
        # axis-level avg is approximate; use avg_p_top as proxy
        weighted_sum += (p_top or 0.0) * weight
    return round(weighted_sum / total_weight, 4)


def get_registry_aggregation(db: Session) -> RegistryAggregationResponse:
    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Risk tier distribution
    tier_rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    risk_tier_distribution = [
        TierCountItem(risk_tier=(r.risk_tier or "UNKNOWN"), count=r.cnt)
        for r in tier_rows
    ]

    # Source x risk_tier breakdown
    source_tier_rows = (
        db.query(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.registry_source, McpServerRegistry.risk_tier)
        .all()
    )
    source_tier_breakdown = [
        SourceTierItem(
            registry_source=r.registry_source,
            risk_tier=(r.risk_tier or "UNKNOWN"),
            count=r.cnt,
        )
        for r in source_tier_rows
    ]

    # Identify scored vs unscored servers
    scored_server_ids = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )
    scored_count = (
        db.query(func.count(scored_server_ids.c.server_id))
        .scalar()
    ) or 0
    unscored_count = total_servers - scored_count

    # Sample unscored servers (up to 20) for inspection
    unscored_sample = (
        db.query(McpServerRegistry)
        .filter(
            McpServerRegistry.server_id.notin_(
                db.query(McpLlmAxisScore.server_id).distinct()
            )
        )
        .limit(20)
        .all()
    )

    unscored_servers_sample = [
        ServerAxisSummary(
            server_id=s.server_id,
            server_name=s.name,
            composite_score=None,
            risk_tier=s.risk_tier or "INSUFFICIENT",
            axis_count=0,
            last_scored_at=None,
        )
        for s in unscored_sample
    ]

    return RegistryAggregationResponse(
        total_servers=total_servers,
        scored_servers=scored_count,
        unscored_servers=unscored_count,
        risk_tier_distribution=risk_tier_distribution,
        source_tier_breakdown=source_tier_breakdown,
        unscored_servers_sample=unscored_servers_sample,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/registry/risk_tier_summary", response_model=RegistryAggregationResponse)
def registry_aggregation_endpoint(
    db: Session = Depends(get_session),
) -> RegistryAggregationResponse:
    """Return aggregated risk tier distribution across the MCP server registry."""
    return get_registry_aggregation(db)


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

    now = datetime.now(timezone.utc)

    with TestSessionLocal() as db:
        # Server with risk_tier set
        s1 = McpServerRegistry(
            server_id="srv-1",
            name="Trusted Alpha",
            registry_source="npx",
            risk_tier="TRUSTED_GENERAL",
        )
        s2 = McpServerRegistry(
            server_id="srv-2",
            name="Risky Beta",
            registry_source="github",
            risk_tier="HIGH_RISK_ISOLATED",
        )
        s3 = McpServerRegistry(
            server_id="srv-3",
            name="Unknown Gamma",
            registry_source="npx",
            risk_tier=None,
        )
        s4 = McpServerRegistry(
            server_id="srv-4",
            name="Unknown Delta",
            registry_source="github",
            risk_tier=None,
        )
        db.add_all([s1, s2, s3, s4])
        db.flush()

        # Add axis scores for srv-1 only
        for axis_name, p_top in [
            ("overall_risk", 0.95),
            ("auth_strength", 0.90),
            ("capability_breadth", 0.85),
            ("data_sensitivity", 0.92),
            ("network_egress", 0.88),
            ("maintainer_trust", 0.94),
            ("exploit_surface", 0.91),
        ]:
            db.add(McpLlmAxisScore(
                server_id="srv-1",
                axis_name=axis_name,
                p_top=p_top,
                p_critical=round(1.0 - p_top - 0.02, 4),
                p_danger=0.02,
                scored_at=now,
            ))

        db.commit()

    client = TestClient(app)

    resp = client.get("/api/registry/risk_tier_summary")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] == 4, f"total_servers={data['total_servers']}"
    assert data["scored_servers"] == 1, f"scored_servers={data['scored_servers']}"
    assert data["unscored_servers"] == 3, f"unscored_servers={data['unscored_servers']}"
    assert len(data["risk_tier_distribution"]) >= 2
    assert len(data["source_tier_breakdown"]) >= 2
    assert len(data["unscored_servers_sample"]) == 3

    # Verify unscored sample does NOT include srv-1
    unscored_ids = {s["server_id"] for s in data["unscored_servers_sample"]}
    assert "srv-1" not in unscored_ids, "srv-1 should be scored, not in unscored sample"

    print("PASS")
    sys.exit(0)
