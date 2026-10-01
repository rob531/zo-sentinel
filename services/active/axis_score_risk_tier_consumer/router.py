# deps: fastapi, pydantic, sqlalchemy
"""Axis Score Risk Tier Consumer.

Consumes axis scores from mcp_llm_axis_scores, derives per-server risk tiers
from the weighted composite of the 7 axes, and exposes the results via a
public REST endpoint.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on McpLlmAxisScore / McpServerRegistry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_score_risk_tier_consumer"])


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
    (0.75, "TRUSTED_GENERAL"),
    (0.60, "TRUSTED_RESEARCH"),
    (0.45, "ENTERPRISE_CONTROLLED"),
    (0.30, "CAUTION_LIMITED"),
    (0.15, "HIGH_RISK_ISOLATED"),
    (0.00, "KNOWN_THREAT"),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _tier_contribution(p_top: float | None, p_critical: float | None, p_danger: float | None) -> float:
    return round((p_top or 0.0) * 100 + (p_critical or 0.0) * 60 + (p_danger or 0.0) * 20, 2)


def _composite_score(axes: list[dict[str, Any]]) -> float:
    total = 0.0
    for ax in axes:
        w = AXIS_WEIGHTS.get(ax["axis_name"], 0.1)
        total += w * ax["contribution"]
    return round(min(total, 100.0), 2)


def _map_to_tier(score: float) -> str:
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if score > threshold:
            return tier
    return "KNOWN_THREAT"


def _fetch_axes(db: Session, server_id: str) -> list[dict[str, Any]]:
    records = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    axes: list[dict[str, Any]] = []
    for r in records:
        axes.append({
            "axis_name": r.axis_name,
            "label": r.label,
            "p_top": r.p_top,
            "p_critical": r.p_critical,
            "p_danger": r.p_danger,
            "contribution": _tier_contribution(r.p_top, r.p_critical, r.p_danger),
        })
    return axes


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisScoreItem(BaseModel):
    axis_name: str
    label: str | None
    p_top: float | None
    p_critical: float | None
    p_danger: float | None
    tier_contribution: float

    model_config = ConfigDict(from_attributes=True)


class ServerRiskTierResponse(BaseModel):
    server_id: str
    server_name: str | None
    composite_score: float
    risk_tier: str
    confidence: float
    axes: list[AxisScoreItem]
    computed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BatchComputeRequest(BaseModel):
    server_ids: list[str] | None = None


class BatchComputeResponse(BaseModel):
    computed_count: int
    tier_distribution: dict[str, int]
    computed_at: datetime


class ScoringHealthResponse(BaseModel):
    status: str
    total_servers: int
    scored_servers: int
    unscored_servers: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/servers/{server_id}/risk-tier", response_model=ServerRiskTierResponse)
def get_server_risk_tier(server_id: str, db: Session = Depends(get_session)):
    """Return computed risk tier for a single server based on its axis scores."""
    server = db.get(McpServerRegistry, server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    axes = _fetch_axes(db, server_id)
    score = _composite_score(axes)
    tier = _map_to_tier(score)
    confidence = round(min(len(axes) / 7.0 + 0.15, 0.98), 2)

    sorted_axes = sorted(
        axes,
        key=lambda a: list(AXIS_WEIGHTS.keys()).index(a["axis_name"])
        if a["axis_name"] in AXIS_WEIGHTS else 99,
    )

    return ServerRiskTierResponse(
        server_id=server_id,
        server_name=server.name,
        composite_score=score,
        risk_tier=tier,
        confidence=confidence,
        axes=[AxisScoreItem(**a) for a in sorted_axes],
        computed_at=datetime.now(timezone.utc),
    )


@router.post("/risk-tier/compute", response_model=BatchComputeResponse)
def compute_all_risk_tiers(request: BatchComputeRequest, db: Session = Depends(get_session)):
    """Recompute and persist risk tiers for all servers or a specified subset."""
    now = datetime.now(timezone.utc)
    if request.server_ids:
        servers = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id.in_(request.server_ids))
            .all()
        )
    else:
        servers = db.query(McpServerRegistry).all()

    distribution: dict[str, int] = {}
    count = 0
    for srv in servers:
        axes = _fetch_axes(db, srv.server_id)
        score = _composite_score(axes)
        tier = _map_to_tier(score)
        srv.risk_tier = tier
        srv.last_assessed = now
        distribution[tier] = distribution.get(tier, 0) + 1
        count += 1

    db.commit()
    return BatchComputeResponse(computed_count=count, tier_distribution=distribution, computed_at=now)


@router.get("/risk-tier/health", response_model=ScoringHealthResponse)
def scoring_health(db: Session = Depends(get_session)):
    """Return basic scoring consumer health stats."""
    from sqlalchemy import func

    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    scored = db.query(func.count(func.distinct(McpLlmAxisScore.server_id))).scalar() or 0
    return ScoringHealthResponse(
        status="ok",
        total_servers=total,
        scored_servers=scored,
        unscored_servers=total - scored,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(test_engine)

    def _override():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    # Seed test data
    now = datetime.now(timezone.utc)
    with TestSessionLocal() as sess:
        sess.add_all([
            McpServerRegistry(server_id="srv-high", name="High Trust"),
            McpServerRegistry(server_id="srv-mid", name="Mid Trust"),
            McpServerRegistry(server_id="srv-low", name="Low Trust"),
            McpServerRegistry(server_id="srv-none", name="No Scores"),
        ])
        sess.flush()

        # srv-high: overall_risk p_top=0.92 -> score ~92 -> TRUSTED_GENERAL
        for ax, lbl, pt, pc, pd in [
            ("overall_risk", "TRUSTED", 0.92, 0.06, 0.02),
            ("auth_strength", "STRONG", 0.88, 0.08, 0.04),
            ("capability_breadth", "BROAD", 0.85, 0.10, 0.05),
            ("data_sensitivity", "LOW", 0.90, 0.08, 0.02),
            ("network_egress", "RESTRICTED", 0.93, 0.05, 0.02),
            ("maintainer_trust", "HIGH", 0.88, 0.09, 0.03),
            ("exploit_surface", "NARROW", 0.86, 0.10, 0.04),
        ]:
            sess.add(McpLlmAxisScore(
                server_id="srv-high", axis_name=ax, label=lbl,
                p_top=pt, p_critical=pc, p_danger=pd,
                model_version="v1", scored_at=now,
            ))

        # srv-mid: axes average p_top ~0.55 -> score ~55 -> ENTERPRISE_CONTROLLED
        for ax, pt in [
            ("auth_strength", 0.55), ("capability_breadth", 0.60),
            ("data_sensitivity", 0.50), ("network_egress", 0.55),
            ("maintainer_trust", 0.52), ("exploit_surface", 0.58),
        ]:
            sess.add(McpLlmAxisScore(
                server_id="srv-mid", axis_name=ax,
                p_top=pt, p_critical=1 - pt - 0.1, p_danger=0.1,
                model_version="v1", scored_at=now,
            ))

        # srv-low: axes average p_top ~0.12 -> score ~12 -> HIGH_RISK_ISOLATED
        for ax, pt in [
            ("auth_strength", 0.12), ("capability_breadth", 0.15),
            ("data_sensitivity", 0.10), ("network_egress", 0.13),
            ("maintainer_trust", 0.11), ("exploit_surface", 0.14),
        ]:
            sess.add(McpLlmAxisScore(
                server_id="srv-low", axis_name=ax,
                p_top=pt, p_critical=0.2, p_danger=1.0 - pt - 0.2,
                model_version="v1", scored_at=now,
            ))
        sess.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override
    client = TestClient(test_app)

    errors: list[str] = []

    # Health
    r = client.get("/api/risk-tier/health")
    if r.status_code != 200:
        errors.append(f"health: {r.status_code}")
    else:
        d = r.json()
        if d["total_servers"] != 4:
            errors.append(f"health total_servers expected 4, got {d['total_servers']}")
        if d["scored_servers"] != 3:
            errors.append(f"health scored_servers expected 3, got {d['scored_servers']}")

    # srv-high -> TRUSTED_GENERAL
    r = client.get("/api/servers/srv-high/risk-tier")
    if r.status_code != 200:
        errors.append(f"srv-high: {r.status_code}")
    elif r.json()["risk_tier"] != "TRUSTED_GENERAL":
        errors.append(f"srv-high tier: {r.json()['risk_tier']}")

    # srv-mid -> ENTERPRISE_CONTROLLED
    r = client.get("/api/servers/srv-mid/risk-tier")
    if r.status_code != 200:
        errors.append(f"srv-mid: {r.status_code}")
    elif r.json()["risk_tier"] != "ENTERPRISE_CONTROLLED":
        errors.append(f"srv-mid tier: {r.json()['risk_tier']}")

    # srv-low -> HIGH_RISK_ISOLATED
    r = client.get("/api/servers/srv-low/risk-tier")
    if r.status_code != 200:
        errors.append(f"srv-low: {r.status_code}")
    elif r.json()["risk_tier"] != "HIGH_RISK_ISOLATED":
        errors.append(f"srv-low tier: {r.json()['risk_tier']}")

    # srv-none (no scores) -> 404
    r = client.get("/api/servers/srv-none/risk-tier")
    if r.status_code != 404:
        errors.append(f"srv-none expected 404, got {r.status_code}")

    # unknown server -> 404
    r = client.get("/api/servers/nobody/risk-tier")
    if r.status_code != 404:
        errors.append(f"nobody expected 404, got {r.status_code}")

    # Batch compute subset
    r = client.post("/api/risk-tier/compute", json={"server_ids": ["srv-high", "srv-mid"]})
    if r.status_code != 200:
        errors.append(f"batch: {r.status_code}")
    else:
        d = r.json()
        if d["computed_count"] != 2:
            errors.append(f"batch count expected 2, got {d['computed_count']}")
        if "TRUSTED_GENERAL" not in d["tier_distribution"]:
            errors.append(f"batch missing TRUSTED_GENERAL: {d['tier_distribution']}")

    # Batch compute all
    r = client.post("/api/risk-tier/compute", json={})
    if r.status_code != 200:
        errors.append(f"compute all: {r.status_code}")
    elif r.json()["computed_count"] != 4:
        errors.append(f"compute all count expected 4, got {r.json()['computed_count']}")

    if errors:
        print("FAIL")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)
