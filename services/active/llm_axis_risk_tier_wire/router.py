# deps: fastapi, pydantic, sqlalchemy
"""llm_axis_risk_tier_wire router.

Wires per-axis scores from mcp_llm_axis_scores to a derived risk_tier,
computing a composite score across the 7 real axes and mapping it to the
PRODUCT_SPEC §2 risk tier taxonomy.

Public endpoint (auth=public per directive).
Data: app tier via get_session + SQLAlchemy ORM (McpLlmAxisScore, McpServerRegistry).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure repo root is on path so app.* imports resolve in all contexts
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["llm_axis_risk_tier_wire"])

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
REAL_AXES = {
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
}

AXIS_WEIGHTS = {
    "overall_risk": 0.25,
    "exploit_surface": 0.20,
    "data_sensitivity": 0.18,
    "auth_strength": 0.15,
    "network_egress": 0.10,
    "maintainer_trust": 0.07,
    "capability_breadth": 0.05,
}

TIER_THRESHOLDS = [
    (75.0, "TRUSTED_GENERAL"),
    (60.0, "TRUSTED_RESEARCH"),
    (45.0, "ENTERPRISE_CONTROLLED"),
    (30.0, "CAUTION_LIMITED"),
    (15.0, "HIGH_RISK_ISOLATED"),
]

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    scored_at: Optional[datetime] = None


class AxisBreakdownOut(BaseModel):
    breakdown: dict[str, float]


class RiskTierWireResponse(BaseModel):
    server_id: str
    composite_score: float
    risk_tier: str
    axes: list[AxisScoreOut]
    breakdown: dict[str, float]
    scored_at: Optional[str] = None


class DistributionResponse(BaseModel):
    distribution: dict[str, int]


class HealthStatus(BaseModel):
    status: str
    service: str = "llm_axis_risk_tier_wire"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _derive_tier(composite: float) -> str:
    for threshold, tier in TIER_THRESHOLDS:
        if composite > threshold:
            return tier
    return "KNOWN_THREAT"


def _build_breakdown(scores: list[McpLlmAxisScore]) -> dict[str, float]:
    return {s.axis_name: float(s.p_top or 0) for s in scores}


def _compute_composite(p_top_by_axis: dict[str, float]) -> float:
    if not p_top_by_axis:
        return 0.0
    total = 0.0
    for axis, p_top in p_top_by_axis.items():
        total += p_top * AXIS_WEIGHTS.get(axis, 0.05)
    return total


def _get_latest_scores(db: Session, server_id: str) -> list[McpLlmAxisScore]:
    """Return the latest score per axis for a server."""
    sub = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    results = (
        db.query(McpLlmAxisScore)
        .join(
            sub,
            (McpLlmAxisScore.server_id == server_id)
            & (McpLlmAxisScore.axis_name == sub.c.axis_name)
            & (McpLlmAxisScore.scored_at == sub.c.max_scored_at),
        )
        .all()
    )
    return results


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get(
    "/llm_axis_risk_tier/{server_id}",
    response_model=RiskTierWireResponse,
)
def get_risk_tier_for_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> RiskTierWireResponse:
    """Return composite score and risk tier for a server from its axis scores."""
    scores = _get_latest_scores(db, server_id)
    if not scores:
        raise HTTPException(status_code=404, detail=f"No axis scores found for server: {server_id}")

    breakdown = _build_breakdown(scores)
    composite = _compute_composite(breakdown)
    risk_tier = _derive_tier(composite)

    axes = [
        AxisScoreOut(
            axis_name=s.axis_name,
            label=s.label,
            p_top=s.p_top,
            p_critical=s.p_critical,
            p_danger=s.p_danger,
            scored_at=s.scored_at,
        )
        for s in scores
    ]

    latest_scored = max((s.scored_at for s in scores if s.scored_at), default=None)

    return RiskTierWireResponse(
        server_id=server_id,
        composite_score=round(composite, 4),
        risk_tier=risk_tier,
        axes=axes,
        breakdown=breakdown,
        scored_at=latest_scored.isoformat() if latest_scored else None,
    )


@router.get(
    "/llm_axis_risk_tier/{server_id}/breakdown",
    response_model=AxisBreakdownOut,
)
def get_breakdown(
    server_id: str,
    db: Session = Depends(get_session),
) -> AxisBreakdownOut:
    """Return just the p_top breakdown by axis for a server."""
    scores = _get_latest_scores(db, server_id)
    if not scores:
        raise HTTPException(status_code=404, detail=f"No axis scores found for server: {server_id}")
    return AxisBreakdownOut(breakdown=_build_breakdown(scores))


@router.get("/llm_axis_risk_tier/distribution", response_model=DistributionResponse)
def get_tier_distribution(
    db: Session = Depends(get_session),
) -> DistributionResponse:
    """Return count of servers per risk tier derived from axis scores."""
    rows = db.query(McpServerRegistry.risk_tier).all()
    dist: dict[str, int] = {}
    for (tier,) in rows:
        key = tier or "UNKNOWN"
        dist[key] = dist.get(key, 0) + 1
    return DistributionResponse(distribution=dist)


@router.get("/llm_axis_risk_tier/health", response_model=HealthStatus)
def health_check() -> HealthStatus:
    return HealthStatus(status="alive")


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    from app.models import Base

    Base.metadata.create_all(bind=test_engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    the_app = FastAPI()
    the_app.include_router(router)
    the_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(the_app)

    # Seed data
    db = TestSessionLocal()
    now = datetime.now(timezone.utc)

    db.add(McpServerRegistry(
        server_id="srv-001", name="Test Server High",
        registry_source="test", url="http://example.com",
        confidence=0.9, risk_tier=None, last_assessed=None,
    ))
    db.add(McpServerRegistry(
        server_id="srv-002", name="Test Server Medium",
        registry_source="test", url="http://example2.com",
        confidence=0.7, risk_tier=None, last_assessed=None,
    ))
    db.add(McpServerRegistry(
        server_id="srv-003", name="Test Server Low",
        registry_source="test", url="http://example3.com",
        confidence=0.5, risk_tier=None, last_assessed=None,
    ))

    mv = "test-model-v1"

    # BigInteger id is NOT auto-increment in SQLite; provide explicit ids
    next_id = [1]

    def _mk_id():
        v = next_id[0]
        next_id[0] += 1
        return v

    # srv-001: high p_top across axes -> high composite -> HIGH_RISK_ISOLATED
    for ax, p in [
        ("overall_risk", 0.92), ("exploit_surface", 0.90), ("data_sensitivity", 0.88),
        ("auth_strength", 0.85), ("network_egress", 0.80), ("maintainer_trust", 0.45),
        ("capability_breadth", 0.75),
    ]:
        db.add(McpLlmAxisScore(
            id=_mk_id(),
            server_id="srv-001", axis_name=ax, label="HIGH",
            p_top=p, p_critical=0.05, p_danger=0.02,
            model_version=mv, scored_at=now,
        ))

    # srv-002: medium p_top -> CAUTION_LIMITED
    for ax, p in [
        ("overall_risk", 0.50), ("exploit_surface", 0.45), ("data_sensitivity", 0.40),
        ("auth_strength", 0.35), ("network_egress", 0.30), ("maintainer_trust", 0.40),
        ("capability_breadth", 0.30),
    ]:
        db.add(McpLlmAxisScore(
            id=_mk_id(),
            server_id="srv-002", axis_name=ax, label="MEDIUM",
            p_top=p, p_critical=0.20, p_danger=0.10,
            model_version=mv, scored_at=now,
        ))

    # srv-003: low p_top -> TRUSTED_GENERAL
    for ax, p in [
        ("overall_risk", 0.10), ("exploit_surface", 0.10), ("data_sensitivity", 0.10),
        ("auth_strength", 0.10), ("network_egress", 0.10), ("maintainer_trust", 0.10),
        ("capability_breadth", 0.10),
    ]:
        db.add(McpLlmAxisScore(
            id=_mk_id(),
            server_id="srv-003", axis_name=ax, label="LOW",
            p_top=p, p_critical=0.05, p_danger=0.02,
            model_version=mv, scored_at=now,
        ))

    db.commit()
    db.close()

    # Test: GET risk tier for srv-001
    r = client.get("/api/llm_axis_risk_tier/srv-001")
    assert r.status_code == 200, f"GET risk_tier failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["server_id"] == "srv-001"
    assert "composite_score" in data
    assert "risk_tier" in data
    assert len(data["axes"]) == 7, f"expected 7 axes, got {len(data['axes'])}"
    assert len(data["breakdown"]) == 7

    # srv-001 has high scores: composite > 15 -> HIGH_RISK_ISOLATED
    assert data["risk_tier"] == "HIGH_RISK_ISOLATED", (
        f"srv-001 expected HIGH_RISK_ISOLATED, got {data['risk_tier']}"
    )

    # Test: GET risk tier for srv-002 (CAUTION_LIMITED, composite between 30-45)
    r2 = client.get("/api/llm_axis_risk_tier/srv-002")
    assert r2.status_code == 200
    assert r2.json()["risk_tier"] == "CAUTION_LIMITED", (
        f"srv-002 expected CAUTION_LIMITED, got {r2.json()['risk_tier']}"
    )

    # Test: GET risk tier for srv-003 (low scores -> TRUSTED_GENERAL)
    r3 = client.get("/api/llm_axis_risk_tier/srv-003")
    assert r3.status_code == 200
    assert r3.json()["risk_tier"] == "TRUSTED_GENERAL", (
        f"srv-003 expected TRUSTED_GENERAL, got {r3.json()['risk_tier']}"
    )

    # Test: 404 for unknown server
    r4 = client.get("/api/llm_axis_risk_tier/unknown-srv")
    assert r4.status_code == 404, f"expected 404 for unknown server, got {r4.status_code}"

    # Test: breakdown endpoint
    r5 = client.get("/api/llm_axis_risk_tier/srv-001/breakdown")
    assert r5.status_code == 200
    bd = r5.json()["breakdown"]
    assert "overall_risk" in bd

    # Test: distribution endpoint
    r6 = client.get("/api/llm_axis_risk_tier/distribution")
    assert r6.status_code == 200
    assert "distribution" in r6.json()

    # Test: health endpoint
    r7 = client.get("/api/llm_axis_risk_tier/health")
    assert r7.status_code == 200
    assert r7.json()["status"] == "alive"

    print("PASS")
