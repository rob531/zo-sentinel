# deps: fastapi, pydantic, sqlalchemy, requests
"""risk_tier_composer_scoring_consumer router.

Consumes new McpLlmAxisScore rows and composes a composite risk_tier for each
server in McpServerRegistry, writing the result back to risk_tier / last_assessed.

Public endpoint (auth=public per the directive).
Data: app tier via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_composer_scoring_consumer"])

HEALTH_SERVICE_URL = "http://127.0.0.1:8772"

# --------------------------------------------------------------------------- #
# Risk tier constants
# --------------------------------------------------------------------------- #
TIER_THRESHOLDS = {
    "LOW": 0.2,
    "MEDIUM": 0.4,
    "ELEVATED": 0.6,
    "HIGH": 0.8,
}

AXIS_WEIGHTS = {
    "CRITICAL": 0.25,
    "HIGH": 0.20,
    "ELEVATED": 0.15,
    "MEDIUM": 0.15,
    "LOW": 0.10,
    "INFO": 0.08,
    "NONE": 0.07,
}

# The 7 real axis names from the schema
REAL_AXES = {
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
}


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class TierUpdate(BaseModel):
    server_id: str
    risk_tier: str
    composite: float


class ComposeResponse(BaseModel):
    processed: int
    updated: list[TierUpdate]


class ServerRiskDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    last_assessed: datetime | None = None
    composite_score: float | None = None
    has_critical: bool = False
    axis_breakdown: dict[str, float] = Field(default_factory=dict)


class RiskTierDistribution(BaseModel):
    distribution: dict[str, int]


class VerdictSummary(BaseModel):
    total: int
    by_verdict: dict[str, int]


class SourceSummary(BaseModel):
    by_source: dict[str, int]


class ServerTimelineEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scored_at: datetime
    axis_name: str
    p_top: float | None = None
    label: str | None = None


class ServerCandidate(BaseModel):
    server_id: str
    risk_tier: str | None = None


class RiskRankingEntry(BaseModel):
    server_id: str
    risk_tier: str | None = None
    composite_score: float | None = None


class HealthStatus(BaseModel):
    status: str
    service: str = "risk_tier_composer_scoring_consumer"


# --------------------------------------------------------------------------- #
# Core computation helpers (no DB, no FastAPI)
# --------------------------------------------------------------------------- #
def compute_composite(p_top_by_axis: dict[str, float]) -> float:
    """Weighted average of p_top across axes."""
    if not p_top_by_axis:
        return 0.0
    total = 0.0
    for axis, p_top in p_top_by_axis.items():
        total += p_top * AXIS_WEIGHTS.get(axis, 0.1)
    return total


def determine_risk_tier(composite: float, has_critical: bool) -> str:
    """Map composite score + critical flag to tier string."""
    if has_critical:
        return "HIGH_RISK_ISOLATED"
    if composite >= TIER_THRESHOLDS["HIGH"]:
        return "HIGH"
    if composite >= TIER_THRESHOLDS["ELEVATED"]:
        return "ELEVATED"
    if composite >= TIER_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    if composite >= TIER_THRESHOLDS["LOW"]:
        return "LOW"
    return "NONE"


def build_p_top_by_axis(scores: list[McpLlmAxisScore]) -> dict[str, float]:
    """Build axis_name -> p_top dict from a list of axis score rows."""
    result: dict[str, float] = {}
    for s in scores:
        result[s.axis_name] = s.p_top
    return result


# --------------------------------------------------------------------------- #
# DB-access layer (pure functions taking Session)
# --------------------------------------------------------------------------- #
def _get_all_servers(db: Session) -> list[McpServerRegistry]:
    return db.query(McpServerRegistry).all()


def _get_latest_scores_for_server(
    db: Session, server_id: str
) -> list[McpLlmAxisScore]:
    """Get the most recent score per axis for a server (by scored_at desc)."""
    from sqlalchemy import func, and_

    # Get max scored_at per axis_name for this server
    sub_q = (
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
            sub_q,
            and_(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name == sub_q.c.axis_name,
                McpLlmAxisScore.scored_at == sub_q.c.max_scored_at,
            ),
        )
        .all()
    )
    return results


def _get_servers_needing_update(db: Session) -> list[McpServerRegistry]:
    """Return servers whose latest score is newer than last_assessed."""
    servers = _get_all_servers(db)
    needs_update = []
    for srv in servers:
        scores = _get_latest_scores_for_server(db, srv.server_id)
        if not scores:
            continue
        latest_score_time = max(s.scored_at for s in scores)
        if srv.last_assessed is None or latest_score_time > srv.last_assessed:
            needs_update.append(srv)
    return needs_update


def _update_server_tier(
    db: Session, server_id: str, risk_tier: str, assessed_at: datetime
) -> None:
    srv = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if srv:
        srv.risk_tier = risk_tier
        srv.last_assessed = assessed_at
        db.commit()


def _get_scores_for_server(
    db: Session, server_id: str
) -> list[McpLlmAxisScore]:
    return (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )


# --------------------------------------------------------------------------- #
# Core daemon logic
# --------------------------------------------------------------------------- #
def run(db: Session) -> dict[str, Any]:
    """Process all servers with new scores and update their risk tiers."""
    results: dict[str, Any] = {"processed": 0, "updated": []}
    servers = _get_servers_needing_update(db)
    now = datetime.now(timezone.utc)
    for srv in servers:
        scores = _get_latest_scores_for_server(db, srv.server_id)
        if not scores:
            continue
        p_top_by_axis = build_p_top_by_axis(scores)
        composite = compute_composite(p_top_by_axis)
        has_critical = "CRITICAL" in p_top_by_axis or any(
            p_top_by_axis.get(ax, 0) >= 0.7
            for ax in ["overall_risk", "exploit_surface"]
        )
        tier = determine_risk_tier(composite, has_critical)
        _update_server_tier(db, srv.server_id, tier, now)
        results["processed"] += 1
        results["updated"].append(
            {"server_id": srv.server_id, "risk_tier": tier, "composite": round(composite, 4)}
        )
    return results


# --------------------------------------------------------------------------- #
# Heartbeat
# --------------------------------------------------------------------------- #
def _send_heartbeat() -> None:
    try:
        requests.post(
            f"{HEALTH_SERVICE_URL}/service_health",
            json={"service": "risk_tier_composer_scoring_consumer", "status": "alive"},
            timeout=5,
        )
    except Exception:
        pass


def _daemon_loop(interval: int = 60) -> None:
    """Background daemon loop (call in a thread, not in __main__)."""
    while True:
        try:
            with next(get_session()) as db:
                run(db)
            _send_heartbeat()
        except Exception:
            pass
        time.sleep(interval)


# --------------------------------------------------------------------------- #
# API Endpoints
# --------------------------------------------------------------------------- #
@router.post("/risk-tier-composer/compose", response_model=ComposeResponse)
def compose_tiers(db: Session = Depends(get_session)) -> ComposeResponse:
    """Trigger a compose run: read new scores and update server risk tiers."""
    result = run(db)
    return ComposeResponse(
        processed=result["processed"],
        updated=[TierUpdate(**u) for u in result["updated"]],
    )


@router.get("/risk-tier-composer/risk/{server_id}", response_model=ServerRiskDetail)
def get_server_risk_detail(
    server_id: str, db: Session = Depends(get_session)
) -> ServerRiskDetail:
    """Get composite risk detail for a single server."""
    srv = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    scores = _get_latest_scores_for_server(db, server_id)
    p_top_by_axis = build_p_top_by_axis(scores)
    composite = compute_composite(p_top_by_axis)
    has_critical = "CRITICAL" in p_top_by_axis

    return ServerRiskDetail(
        server_id=server_id,
        name=srv.name,
        risk_tier=srv.risk_tier,
        last_assessed=srv.last_assessed,
        composite_score=round(composite, 4),
        has_critical=has_critical,
        axis_breakdown={k: round(v, 4) for k, v in p_top_by_axis.items()},
    )


@router.get("/risk-tier-composer/distribution", response_model=RiskTierDistribution)
def get_distribution(db: Session = Depends(get_session)) -> RiskTierDistribution:
    """Get count of servers per risk tier."""
    servers = _get_all_servers(db)
    dist: dict[str, int] = {}
    for s in servers:
        tier = s.risk_tier or "NONE"
        dist[tier] = dist.get(tier, 0) + 1
    return RiskTierDistribution(distribution=dist)


@router.get("/risk-tier-composer/verdicts", response_model=VerdictSummary)
def get_verdict_summary(db: Session = Depends(get_session)) -> VerdictSummary:
    """Get server count grouped by verdict."""
    servers = _get_all_servers(db)
    by_verdict: dict[str, int] = {}
    for s in servers:
        v = s.verdict or "unknown"
        by_verdict[v] = by_verdict.get(v, 0) + 1
    return VerdictSummary(total=len(servers), by_verdict=by_verdict)


@router.get("/risk-tier-composer/sources", response_model=SourceSummary)
def get_source_summary(db: Session = Depends(get_session)) -> SourceSummary:
    """Get server count grouped by registry source."""
    servers = _get_all_servers(db)
    by_source: dict[str, int] = {}
    for s in servers:
        src = s.registry_source or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    return SourceSummary(by_source=by_source)


@router.get(
    "/risk-tier-composer/timeline/{server_id}",
    response_model=list[ServerTimelineEntry],
)
def get_server_timeline(
    server_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> list[ServerTimelineEntry]:
    """Get score timeline for a server (most recent first)."""
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit)
        .all()
    )
    return [
        ServerTimelineEntry(
            scored_at=s.scored_at,
            axis_name=s.axis_name,
            p_top=s.p_top,
            label=s.label,
        )
        for s in scores
    ]


@router.get(
    "/risk-tier-composer/candidates",
    response_model=list[ServerCandidate],
)
def get_candidates(
    min_tier: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[ServerCandidate]:
    """List servers optionally filtered by minimum risk tier."""
    tier_order = ["NONE", "LOW", "MEDIUM", "ELEVATED", "HIGH", "HIGH_RISK_ISOLATED"]
    servers = _get_all_servers(db)
    if min_tier and min_tier in tier_order:
        min_idx = tier_order.index(min_tier)
        servers = [
            s for s in servers
            if s.risk_tier in tier_order[min_idx:] or (s.risk_tier is None and min_idx == 0)
        ]
    return [ServerCandidate(server_id=s.server_id, risk_tier=s.risk_tier) for s in servers]


@router.get(
    "/risk-tier-composer/ranking",
    response_model=list[RiskRankingEntry],
)
def get_risk_ranking(
    limit: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(get_session),
) -> list[RiskRankingEntry]:
    """Rank all servers by risk tier (highest risk first)."""
    tier_order = ["HIGH_RISK_ISOLATED", "HIGH", "ELEVATED", "MEDIUM", "LOW", "NONE", None]
    servers = sorted(
        _get_all_servers(db),
        key=lambda s: tier_order.index(s.risk_tier),
    )
    results = []
    for s in servers[:limit]:
        scores = _get_latest_scores_for_server(db, s.server_id)
        p_top_by_axis = build_p_top_by_axis(scores)
        composite = round(compute_composite(p_top_by_axis), 4) if p_top_by_axis else None
        results.append(
            RiskRankingEntry(
                server_id=s.server_id,
                risk_tier=s.risk_tier,
                composite_score=composite,
            )
        )
    return results


@router.get("/risk-tier-composer/health", response_model=HealthStatus)
def health_check() -> HealthStatus:
    """Liveness probe for this service."""
    return HealthStatus(status="alive")


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path

    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    from app.models import Base

    Base.metadata.create_all(bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build a LOCAL FastAPI app for the self-test
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(test_app)

    # ---- Seed data ----
    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)

    srv1 = McpServerRegistry(
        server_id="srv-001", name="High Risk Server",
        url="http://localhost:9001", registry_source="test",
        risk_tier="MEDIUM", last_assessed=datetime(2024, 1, 1),
    )
    srv2 = McpServerRegistry(
        server_id="srv-002", name="Medium Risk Server",
        url="http://localhost:9002", registry_source="test",
        risk_tier="LOW", last_assessed=datetime(2024, 1, 1),
    )
    srv3 = McpServerRegistry(
        server_id="srv-003", name="Low Risk Server",
        url="http://localhost:9003", registry_source="test",
        risk_tier="LOW", last_assessed=datetime(2024, 1, 1),
    )
    db.add_all([srv1, srv2, srv3])

    # High composite: srv-001
    db.add_all([
        McpLlmAxisScore(server_id="srv-001", axis_name="overall_risk", label="HIGH", p_top=0.9, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="auth_strength", label="HIGH", p_top=0.8, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="capability_breadth", label="ELEVATED", p_top=0.6, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="data_sensitivity", label="MEDIUM", p_top=0.4, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="network_egress", label="LOW", p_top=0.2, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="maintainer_trust", label="LOW", p_top=0.2, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="exploit_surface", label="HIGH", p_top=0.8, scored_at=now),
    ])

    # Medium composite: srv-002
    db.add_all([
        McpLlmAxisScore(server_id="srv-002", axis_name="overall_risk", label="MEDIUM", p_top=0.45, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="auth_strength", label="MEDIUM", p_top=0.4, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="capability_breadth", label="MEDIUM", p_top=0.45, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="data_sensitivity", label="LOW", p_top=0.2, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="network_egress", label="LOW", p_top=0.2, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="maintainer_trust", label="MEDIUM", p_top=0.4, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="exploit_surface", label="MEDIUM", p_top=0.4, scored_at=now),
    ])

    # Low composite: srv-003
    db.add_all([
        McpLlmAxisScore(server_id="srv-003", axis_name="overall_risk", label="LOW", p_top=0.1, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="auth_strength", label="LOW", p_top=0.15, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="capability_breadth", label="LOW", p_top=0.1, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="data_sensitivity", label="LOW", p_top=0.1, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="network_egress", label="LOW", p_top=0.1, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="maintainer_trust", label="LOW", p_top=0.1, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="exploit_surface", label="LOW", p_top=0.1, scored_at=now),
    ])

    db.commit()
    db.close()

    # ---- Test compose endpoint ----
    resp = client.post("/api/risk-tier-composer/compose")
    assert resp.status_code == 200, f"compose failed: {resp.status_code} {resp.text}"
    compose_data = resp.json()
    assert compose_data["processed"] == 3, f"expected 3 processed, got {compose_data}"

    # ---- Test ranking endpoint ----
    resp2 = client.get("/api/risk-tier-composer/ranking")
    assert resp2.status_code == 200, f"ranking failed: {resp2.status_code}"
    ranking = resp2.json()
    assert len(ranking) == 3, f"expected 3 in ranking, got {len(ranking)}"
    # srv-001 should be first (highest risk)
    assert ranking[0]["server_id"] == "srv-001", f"expected srv-001 first, got {ranking[0]['server_id']}"

    # ---- Test distribution endpoint ----
    resp3 = client.get("/api/risk-tier-composer/distribution")
    assert resp3.status_code == 200
    dist = resp3.json()["distribution"]
    assert "HIGH" in dist or "ELEVATED" in dist or "MEDIUM" in dist, f"no risk tiers in distribution: {dist}"

    # ---- Test health ----
    resp4 = client.get("/api/risk-tier-composer/health")
    assert resp4.status_code == 200
    assert resp4.json()["status"] == "alive"

    print("PASS")
