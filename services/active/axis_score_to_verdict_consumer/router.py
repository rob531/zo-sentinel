# deps: fastapi, pydantic, sqlalchemy, requests
"""axis_score_to_verdict_consumer router.

Consumes per-axis scores from mcp_llm_axis_scores, computes a composite risk score
across the 7 real axes, derives risk_tier per PRODUCT_SPEC §2 taxonomy, and upserts
the result to mcp_server_registry.risk_tier / last_assessed.

Public endpoint (auth=public per the directive).
Data: app tier via get_session + SQLAlchemy ORM (McpLlmAxisScore, McpServerRegistry).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root BEFORE any app.* imports so that 'app' is resolvable
_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import requests
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# Import trust gating (pure-stdlib, no app deps)
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["axis_score_to_verdict_consumer"])

HEALTH_SERVICE_URL = "http://127.0.0.1:8772"

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
class ConsumeRequest(BaseModel):
    server_ids: list[str] = Field(default_factory=list)


class VerdictEntry(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str
    overall_risk: float
    axis_breakdown: dict[str, float] = Field(default_factory=dict)


class ConsumeResponse(BaseModel):
    consumed: int
    verdicts: list[VerdictEntry]


class ServerAxisDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    axis_name: str
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    scored_at: datetime | None = None


class AxisBreakdown(BaseModel):
    breakdown: dict[str, float]


class RiskTierDistribution(BaseModel):
    distribution: dict[str, int]


class HealthStatus(BaseModel):
    status: str
    service: str = "axis_score_to_verdict_consumer"


# --------------------------------------------------------------------------- #
# Core computation helpers (no DB, no FastAPI)
# --------------------------------------------------------------------------- #
def _derive_risk_tier(composite: float) -> str:
    for threshold, tier in TIER_THRESHOLDS:
        if composite > threshold:
            return tier
    return "KNOWN_THREAT"


def _compute_composite(p_top_by_axis: dict[str, float]) -> float:
    if not p_top_by_axis:
        return 0.0
    total = 0.0
    for axis, p_top in p_top_by_axis.items():
        total += p_top * AXIS_WEIGHTS.get(axis, 0.05)
    return total


def _apply_trust_gate(
    url: str | None,
    name: str | None,
    p_top_by_axis: dict[str, float],
) -> tuple[float, str]:
    axis_labels: dict[str, str] = {}
    for ax in REAL_AXES:
        p = p_top_by_axis.get(ax, 0.0)
        if p >= 0.7:
            axis_labels[ax] = "HIGH"
        elif p >= 0.4:
            axis_labels[ax] = "MEDIUM"
        else:
            axis_labels[ax] = "LOW"

    gate_record = trust_gate(url, name, axis_labels)
    capped_label = gate_record.get("published_overall_risk", "HIGH")
    label_to_score = {"LOW": 0.1, "MEDIUM": 0.4, "HIGH": 0.8, "CRITICAL": 1.0}
    composite = label_to_score.get(capped_label, 0.5)
    risk_tier = _derive_risk_tier(composite * 100)
    return composite, risk_tier


# --------------------------------------------------------------------------- #
# DB-access layer
# --------------------------------------------------------------------------- #
def _get_latest_model_version(db: Session) -> str | None:
    row = (
        db.query(func.max(McpLlmAxisScore.model_version))
        .scalar_one_or_none()
    )
    return row


def _get_servers_needing_update(
    db: Session,
    server_ids: list[str] | None = None,
) -> list[str]:
    sub = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .subquery()
    )

    latest_score = (
        db.query(McpLlmAxisScore.server_id, McpLlmAxisScore.scored_at)
        .join(sub, McpLlmAxisScore.server_id == sub.c.server_id)
        .filter(McpLlmAxisScore.scored_at == sub.c.max_scored_at)
        .distinct()
        .subquery()
    )

    reg_q = db.query(McpServerRegistry.server_id, McpServerRegistry.last_assessed)
    if server_ids:
        reg_q = reg_q.filter(McpServerRegistry.server_id.in_(server_ids))

    results: list[str] = []
    for srv_id, last_assessed in reg_q.all():
        score_row = (
            db.query(latest_score.c.scored_at)
            .filter(latest_score.c.server_id == srv_id)
            .scalar_one_or_none()
        )
        if score_row is None:
            continue
        if last_assessed is None or score_row > last_assessed:
            results.append(srv_id)

    return results


def _get_axis_scores_for_server(db: Session, server_id: str) -> list[McpLlmAxisScore]:
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


def _build_p_top_by_axis(scores: list[McpLlmAxisScore]) -> dict[str, float]:
    return {s.axis_name: float(s.p_top or 0) for s in scores}


def _update_server_registry(
    db: Session,
    server_id: str,
    risk_tier: str,
    overall_risk: float,
    assessed_at: datetime,
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


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def run(db: Session, server_ids: list[str] | None = None) -> dict[str, Any]:
    servers_to_update = _get_servers_needing_update(db, server_ids)

    verdicts: list[dict] = []
    consumed = 0
    now = datetime.now(timezone.utc)

    for sid in servers_to_update:
        scores = _get_axis_scores_for_server(db, sid)
        if not scores:
            continue

        p_top_by_axis = _build_p_top_by_axis(scores)

        srv_row = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == sid)
            .first()
        )
        url = srv_row.url if srv_row else None
        name = srv_row.name if srv_row else None

        composite, risk_tier = _apply_trust_gate(url, name, p_top_by_axis)
        overall_risk = round(composite * 100, 2)

        _update_server_registry(db, sid, risk_tier, overall_risk, now)

        verdicts.append({
            "server_id": sid,
            "name": name,
            "risk_tier": risk_tier,
            "overall_risk": overall_risk,
            "axis_breakdown": p_top_by_axis,
        })
        consumed += 1

    return {"consumed": consumed, "verdicts": verdicts}


# --------------------------------------------------------------------------- #
# Heartbeat
# --------------------------------------------------------------------------- #
def _send_heartbeat() -> None:
    try:
        requests.post(
            f"{HEALTH_SERVICE_URL}/service_health",
            json={"service": "axis_score_to_verdict_consumer", "status": "alive"},
            timeout=5,
        )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# API Endpoints
# --------------------------------------------------------------------------- #
@router.post("/axis-score-to-verdict/consume", response_model=ConsumeResponse)
def consume_scores(
    request: ConsumeRequest,
    db: Session = Depends(get_session),
) -> ConsumeResponse:
    server_ids: list[str] | None = request.server_ids if request.server_ids else None
    result = run(db, server_ids)
    return ConsumeResponse(
        consumed=result["consumed"],
        verdicts=[VerdictEntry(**v) for v in result["verdicts"]],
    )


@router.get(
    "/axis-score-to-verdict/server/{server_id}/axes",
    response_model=list[ServerAxisDetail],
)
def get_server_axes(
    server_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_session),
) -> list[ServerAxisDetail]:
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit)
        .all()
    )
    return [ServerAxisDetail(
        server_id=s.server_id,
        axis_name=s.axis_name,
        label=s.label,
        p_top=s.p_top,
        p_critical=s.p_critical,
        p_danger=s.p_danger,
        scored_at=s.scored_at,
    ) for s in scores]


@router.get(
    "/axis-score-to-verdict/server/{server_id}/breakdown",
    response_model=AxisBreakdown,
)
def get_server_breakdown(
    server_id: str,
    db: Session = Depends(get_session),
) -> AxisBreakdown:
    model_version = _get_latest_model_version(db) or ""
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.model_version == model_version)
        .all()
    )
    breakdown = _build_p_top_by_axis(scores)
    return AxisBreakdown(breakdown=breakdown)


@router.get("/axis-score-to-verdict/distribution", response_model=RiskTierDistribution)
def get_tier_distribution(
    db: Session = Depends(get_session),
) -> RiskTierDistribution:
    servers = db.query(McpServerRegistry.risk_tier).all()
    dist: dict[str, int] = {}
    for (tier,) in servers:
        key = tier or "UNKNOWN"
        dist[key] = dist.get(key, 0) + 1
    return RiskTierDistribution(distribution=dist)


@router.get("/axis-score-to-verdict/health", response_model=HealthStatus)
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
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    from app.models import Base

    Base.metadata.create_all(bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(test_app)

    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)

    srv1 = McpServerRegistry(
        server_id="srv-001",
        name="High Risk Server",
        url="http://localhost:9001",
        registry_source="test",
        risk_tier=None,
        last_assessed=None,
    )
    srv2 = McpServerRegistry(
        server_id="srv-002",
        name="Medium Risk Server",
        url="http://localhost:9002",
        registry_source="test",
        risk_tier=None,
        last_assessed=None,
    )
    srv3 = McpServerRegistry(
        server_id="srv-003",
        name="Official Stripe Server",
        url="https://stripe.com/mcp",
        registry_source="official",
        risk_tier=None,
        last_assessed=None,
    )
    db.add_all([srv1, srv2, srv3])

    model_ver = "test-model-v1"

    db.add_all([
        McpLlmAxisScore(server_id="srv-001", axis_name="overall_risk", label="HIGH", p_top=0.9,
                         p_critical=0.05, p_danger=0.02, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="auth_strength", label="HIGH", p_top=0.85,
                         p_critical=0.08, p_danger=0.02, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="capability_breadth", label="HIGH", p_top=0.80,
                         p_critical=0.10, p_danger=0.03, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="data_sensitivity", label="HIGH", p_top=0.88,
                         p_critical=0.07, p_danger=0.02, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="network_egress", label="HIGH", p_top=0.82,
                         p_critical=0.09, p_danger=0.03, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="maintainer_trust", label="MEDIUM", p_top=0.45,
                         p_critical=0.20, p_danger=0.10, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-001", axis_name="exploit_surface", label="HIGH", p_top=0.87,
                         p_critical=0.08, p_danger=0.02, model_version=model_ver, scored_at=now),
    ])

    db.add_all([
        McpLlmAxisScore(server_id="srv-002", axis_name="overall_risk", label="MEDIUM", p_top=0.45,
                         p_critical=0.20, p_danger=0.10, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="auth_strength", label="MEDIUM", p_top=0.40,
                         p_critical=0.25, p_danger=0.12, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="capability_breadth", label="MEDIUM", p_top=0.45,
                         p_critical=0.22, p_danger=0.10, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="data_sensitivity", label="LOW", p_top=0.20,
                         p_critical=0.30, p_danger=0.15, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="network_egress", label="LOW", p_top=0.20,
                         p_critical=0.30, p_danger=0.15, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="maintainer_trust", label="MEDIUM", p_top=0.40,
                         p_critical=0.25, p_danger=0.12, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-002", axis_name="exploit_surface", label="MEDIUM", p_top=0.40,
                         p_critical=0.25, p_danger=0.12, model_version=model_ver, scored_at=now),
    ])

    db.add_all([
        McpLlmAxisScore(server_id="srv-003", axis_name="overall_risk", label="HIGH", p_top=0.85,
                         p_critical=0.10, p_danger=0.03, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="auth_strength", label="HIGH", p_top=0.80,
                         p_critical=0.12, p_danger=0.04, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="capability_breadth", label="HIGH", p_top=0.90,
                         p_critical=0.06, p_danger=0.02, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="data_sensitivity", label="HIGH", p_top=0.88,
                         p_critical=0.07, p_danger=0.02, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="network_egress", label="HIGH", p_top=0.82,
                         p_critical=0.09, p_danger=0.03, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="maintainer_trust", label="HIGH", p_top=0.90,
                         p_critical=0.05, p_danger=0.02, model_version=model_ver, scored_at=now),
        McpLlmAxisScore(server_id="srv-003", axis_name="exploit_surface", label="HIGH", p_top=0.85,
                         p_critical=0.08, p_danger=0.03, model_version=model_ver, scored_at=now),
    ])

    db.commit()
    db.close()

    # Test consume endpoint
    resp = client.post("/api/axis-score-to-verdict/consume", json={"server_ids": []})
    assert resp.status_code == 200, f"consume failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["consumed"] == 3, f"expected 3 consumed, got {data['consumed']}"
    assert len(data["verdicts"]) == 3

    # srv-001: untrusted, high composite -> HIGH_RISK_ISOLATED
    v1 = next(v for v in data["verdicts"] if v["server_id"] == "srv-001")
    assert v1["risk_tier"] == "HIGH_RISK_ISOLATED", f"srv-001 got {v1['risk_tier']}"

    # srv-002: medium composite -> CAUTION_LIMITED
    v2 = next(v for v in data["verdicts"] if v["server_id"] == "srv-002")
    assert v2["risk_tier"] == "CAUTION_LIMITED", f"srv-002 got {v2['risk_tier']}"

    # srv-003: stripe.com is a verified publisher -> trust-gated
    v3 = next(v for v in data["verdicts"] if v["server_id"] == "srv-003")
    assert v3["risk_tier"] == "ENTERPRISE_CONTROLLED", f"srv-003 got {v3['risk_tier']}"

    # Test axes endpoint
    resp2 = client.get("/api/axis-score-to-verdict/server/srv-001/axes")
    assert resp2.status_code == 200
    axes = resp2.json()
    assert len(axes) == 7, f"expected 7 axes, got {len(axes)}"

    # Test breakdown endpoint
    resp3 = client.get("/api/axis-score-to-verdict/server/srv-001/breakdown")
    assert resp3.status_code == 200
    bd = resp3.json()["breakdown"]
    assert "overall_risk" in bd

    # Test distribution endpoint
    resp4 = client.get("/api/axis-score-to-verdict/distribution")
    assert resp4.status_code == 200
    dist = resp4.json()["distribution"]
    assert any(v in dist for v in ["HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "ENTERPRISE_CONTROLLED"]), (
        f"no risk tiers in distribution: {dist}"
    )

    # Test health
    resp5 = client.get("/api/axis-score-to-verdict/health")
    assert resp5.status_code == 200
    assert resp5.json()["status"] == "alive"

    print("PASS")
