# deps: fastapi, pydantic, sqlalchemy
"""services.active.risk_tier_composition_api.router

GET /api/risk-tier-composition/server/{server_id}
  Per-server risk tier composition: composite score + axis breakdown + risk tier.

GET /api/risk-tier-composition/summary
  Global risk-tier distribution across all servers.

GET /api/risk-tier-composition/by-source
  Risk-tier distribution broken down by registry_source.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry /
      mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/risk-tier-composition", tags=["risk_tier_composition_api"])


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
AXIS_WEIGHTS: dict[str, float] = {
    "overall_risk": 0.25,
    "auth_strength": 0.12,
    "capability_breadth": 0.10,
    "data_sensitivity": 0.18,
    "network_egress": 0.15,
    "maintainer_trust": 0.12,
    "exploit_surface": 0.08,
}

RISK_TIER_THRESHOLDS: list[tuple[float, str]] = [
    (0.90, "TRUSTED_GENERAL"),
    (0.75, "TRUSTED_RESEARCH"),
    (0.60, "ENTERPRISE_CONTROLLED"),
    (0.40, "CAUTION_LIMITED"),
    (0.20, "HIGH_RISK_ISOLATED"),
    (0.00, "INSUFFICIENT"),
]


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisBreakdown(BaseModel):
    axis_name: str
    p_top: float = Field(..., ge=0, le=1)
    p_critical: float = Field(..., ge=0, le=1)
    p_danger: float = Field(..., ge=0, le=1)
    label: str


class ServerCompositionResponse(BaseModel):
    server_id: str
    server_name: str | None
    composite_score: float = Field(..., ge=0, le=1)
    risk_tier: str
    axis_breakdown: list[AxisBreakdown]
    criteria_version: str = "v1"
    scored_at: str


class TierBucket(BaseModel):
    tier: str
    count: int
    pct: float = Field(..., ge=0, le=100)


class SourceTierRow(BaseModel):
    source: str
    tier: str
    count: int


class SummaryResponse(BaseModel):
    generated_at: str
    total_servers: int
    tiers: list[TierBucket]


class BySourceResponse(BaseModel):
    generated_at: str
    sources: list[SourceTierRow]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _label_from_p_top(p_top: float) -> str:
    if p_top >= 0.7:
        return "excellent"
    if p_top >= 0.5:
        return "good"
    if p_top >= 0.3:
        return "moderate"
    if p_top >= 0.1:
        return "concerning"
    return "critical"


def _compute_composite(axis_data: list[dict[str, Any]]) -> tuple[float, list[AxisBreakdown]]:
    if not axis_data:
        return 0.0, []

    breakdown: list[AxisBreakdown] = []
    weighted_sum = 0.0
    total_weight = 0.0

    for row in axis_data:
        axis_name = row["axis_name"]
        p_top = row.get("p_top") or 0.0
        p_critical = row.get("p_critical") or 0.0
        p_danger = row.get("p_danger") or 0.0

        breakdown.append(AxisBreakdown(
            axis_name=axis_name,
            p_top=round(p_top, 4),
            p_critical=round(p_critical, 4),
            p_danger=round(p_danger, 4),
            label=_label_from_p_top(p_top),
        ))

        weight = AXIS_WEIGHTS.get(axis_name, 0.1)
        weighted_sum += p_top * weight
        total_weight += weight

    composite = weighted_sum / total_weight if total_weight > 0 else 0.0
    return round(composite, 4), breakdown


def _map_to_risk_tier(score: float) -> str:
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "INSUFFICIENT"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get(
    "/server/{server_id}",
    response_model=ServerCompositionResponse,
    name="risk_tier_composition:server",
)
def get_server_composition(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerCompositionResponse:
    """Return composite score, per-axis breakdown, and risk tier for one server."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axis_scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(7)
        .all()
    )

    if not axis_scores:
        return ServerCompositionResponse(
            server_id=server_id,
            server_name=server.name,
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

    latest = axis_scores[0].scored_at
    scored_at_str = (
        latest.isoformat()
        if isinstance(latest, datetime)
        else str(latest)
    )

    composite_score, breakdown = _compute_composite(axis_data)
    risk_tier = _map_to_risk_tier(composite_score)

    return ServerCompositionResponse(
        server_id=server_id,
        server_name=server.name,
        composite_score=composite_score,
        risk_tier=risk_tier,
        axis_breakdown=breakdown,
        criteria_version="v1",
        scored_at=scored_at_str,
    )


@router.get(
    "/summary",
    response_model=SummaryResponse,
    name="risk_tier_composition:summary",
)
def get_composition_summary(
    db: Session = Depends(get_session),
) -> SummaryResponse:
    """Return global risk-tier distribution (count + pct) across all servers."""
    now = datetime.now(timezone.utc).isoformat()

    total = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar() or 0

    if total == 0:
        return SummaryResponse(generated_at=now, total_servers=0, tiers=[])

    rows = db.execute(
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        ).group_by(McpServerRegistry.risk_tier)
    ).all()

    counts: dict[str, int] = {}
    for row in rows:
        tier = row.risk_tier or "UNKNOWN"
        counts[tier] = row.cnt

    tiers = [
        TierBucket(tier=tier, count=cnt, pct=round((cnt / total) * 100, 2))
        for tier, cnt in sorted(counts.items(), key=lambda x: -x[1])
    ]

    return SummaryResponse(generated_at=now, total_servers=total, tiers=tiers)


@router.get(
    "/by-source",
    response_model=BySourceResponse,
    name="risk_tier_composition:by_source",
)
def get_composition_by_source(
    db: Session = Depends(get_session),
) -> BySourceResponse:
    """Return risk-tier distribution broken down by registry_source."""
    now = datetime.now(timezone.utc).isoformat()

    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        ).group_by(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
    ).all()

    sources: list[SourceTierRow] = []
    for row in rows:
        sources.append(SourceTierRow(
            source=row.registry_source or "unknown",
            tier=row.risk_tier or "UNKNOWN",
            count=row.cnt,
        ))

    return BySourceResponse(generated_at=now, sources=sources)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    _app = FastAPI()
    _app.include_router(router)

    def _override():
        db = _TestSession()
        try:
            yield db
        finally:
            db.close()

    _app.dependency_overrides[get_session] = _override
    _client = TestClient(_app)

    # Seed data
    with _TestSession() as _db:
        _servers = [
            McpServerRegistry(
                server_id="srv-trusted", name="Trusted Srv",
                registry_source="npm", risk_tier="TRUSTED_GENERAL",
            ),
            McpServerRegistry(
                server_id="srv-risky", name="Risky Srv",
                registry_source="github", risk_tier="HIGH_RISK_ISOLATED",
            ),
            McpServerRegistry(
                server_id="srv-unknown", name="Unknown Srv",
                registry_source="npm", risk_tier=None,
            ),
        ]
        for _s in _servers:
            _db.add(_s)
        _db.flush()

        _now = datetime.now(timezone.utc)

        _high_axes = [
            ("overall_risk", 0.95, 0.03, 0.02),
            ("auth_strength", 0.90, 0.05, 0.05),
            ("capability_breadth", 0.85, 0.08, 0.07),
            ("data_sensitivity", 0.92, 0.04, 0.04),
            ("network_egress", 0.88, 0.06, 0.06),
            ("maintainer_trust", 0.94, 0.03, 0.03),
            ("exploit_surface", 0.91, 0.05, 0.04),
        ]
        for _an, _pt, _pc, _pd in _high_axes:
            _db.add(McpLlmAxisScore(
                server_id="srv-trusted", axis_name=_an,
                p_top=_pt, p_critical=_pc, p_danger=_pd, scored_at=_now,
            ))

        _low_axes = [
            ("overall_risk", 0.12, 0.28, 0.60),
            ("auth_strength", 0.15, 0.25, 0.60),
            ("capability_breadth", 0.18, 0.22, 0.60),
            ("data_sensitivity", 0.10, 0.30, 0.60),
            ("network_egress", 0.20, 0.20, 0.60),
            ("maintainer_trust", 0.08, 0.32, 0.60),
            ("exploit_surface", 0.05, 0.35, 0.60),
        ]
        for _an, _pt, _pc, _pd in _low_axes:
            _db.add(McpLlmAxisScore(
                server_id="srv-risky", axis_name=_an,
                p_top=_pt, p_critical=_pc, p_danger=_pd, scored_at=_now,
            ))

        _db.commit()

    # Test 1: per-server composition -- trusted server
    _r1 = _client.get("/api/risk-tier-composition/server/srv-trusted")
    assert _r1.status_code == 200, f"trusted 200: {_r1.status_code} {_r1.text}"
    _d1 = _r1.json()
    assert _d1["risk_tier"] == "TRUSTED_GENERAL", (
        f"Expected TRUSTED_GENERAL, got {_d1['risk_tier']}"
    )
    assert _d1["server_id"] == "srv-trusted"
    assert len(_d1["axis_breakdown"]) == 7
    assert 0.0 <= _d1["composite_score"] <= 1.0

    # Test 2: per-server composition -- risky server
    _r2 = _client.get("/api/risk-tier-composition/server/srv-risky")
    assert _r2.status_code == 200, f"risky 200: {_r2.status_code}"
    _d2 = _r2.json()
    assert _d2["risk_tier"] == "HIGH_RISK_ISOLATED", (
        f"Expected HIGH_RISK_ISOLATED, got {_d2['risk_tier']}"
    )
    assert _d2["server_id"] == "srv-risky"

    # Test 3: server with no axis scores -> INSUFFICIENT
    _r3 = _client.get("/api/risk-tier-composition/server/srv-unknown")
    assert _r3.status_code == 200, f"unknown 200: {_r3.status_code}"
    _d3 = _r3.json()
    assert _d3["risk_tier"] == "INSUFFICIENT", (
        f"Expected INSUFFICIENT, got {_d3['risk_tier']}"
    )

    # Test 4: nonexistent server -> 404
    _r4 = _client.get("/api/risk-tier-composition/server/does-not-exist")
    assert _r4.status_code == 404, f"404 expected, got {_r4.status_code}"

    # Test 5: summary endpoint
    _r5 = _client.get("/api/risk-tier-composition/summary")
    assert _r5.status_code == 200, f"summary 200: {_r5.status_code}"
    _d5 = _r5.json()
    assert _d5["total_servers"] == 3, f"total_servers=3, got {_d5['total_servers']}"
    assert len(_d5["tiers"]) > 0
    for _t in _d5["tiers"]:
        assert "tier" in _t
        assert "count" in _t
        assert "pct" in _t

    # Test 6: by-source endpoint
    _r6 = _client.get("/api/risk-tier-composition/by-source")
    assert _r6.status_code == 200, f"by-source 200: {_r6.status_code}"
    _d6 = _r6.json()
    assert "sources" in _d6
    assert len(_d6["sources"]) > 0

    # Test 7: auth failure (no session override)
    _app.dependency_overrides.clear()
    _r7 = _client.get("/api/risk-tier-composition/summary")
    if _r7.status_code == 200:
        print("FAIL: expected non-200 without session override")
        _sys.exit(1)

    print("PASS")
    _sys.exit(0)
