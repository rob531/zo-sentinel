# deps: fastapi, pydantic, sqlalchemy
"""risk_tier_verdict_endpoint – public API returning per-server risk tier
verdict with trust-gating applied.

Endpoints
─────────
GET /api/risk-tier-verdict/{server_id}
    Returns the risk tier and axis-level verdict for one MCP server,
    including trust-gating calibration (verified publishers capped at MEDIUM,
    masquerade homoglyphs flagged).  Reads from mcp_server_registry and
    mcp_llm_axis_scores via the app DB session.

GET /api/risk-tier-verdict/{server_id}/axes
    Returns the raw per-axis score rows for the current model version.

GET /api/risk-tier-verdict/summary
    Returns global risk-tier distribution across all scored servers.

Public endpoint — no auth required (PRODUCT_SPEC §9 scope).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk_tier_verdict_endpoint"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisVerdict(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[str] = None

    class Config:
        from_attributes = True


class TrustGateInfo(BaseModel):
    original_overall_risk: Optional[str] = None
    published_overall_risk: Optional[str] = None
    capped: bool = False
    trusted: bool = False
    trust_basis: Optional[str] = None
    masquerade_flag: bool = False
    display_label: str = "Automated heuristic assessment"


class ServerRiskTierVerdict(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    trust_score: Optional[float] = None
    last_assessed: Optional[str] = None
    axes: dict[str, AxisVerdict] = Field(default_factory=dict)
    trust_gate: Optional[TrustGateInfo] = None


class AxisVerdictResponse(BaseModel):
    server_id: str
    model_version: Optional[str] = None
    axes: list[AxisVerdict]


class TierCount(BaseModel):
    tier: str
    count: int
    percentage: float


class RiskTierSummary(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    tiers: list[TierCount]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = (
        db.execute(
            select(McpLlmAxisScore.model_version)
            .where(McpLlmAxisScore.server_id == server_id)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    return row


def _build_axis_dict(rows: list[McpLlmAxisScore]) -> dict[str, AxisVerdict]:
    result: dict[str, AxisVerdict] = {}
    seen: set[str] = set()
    # take the latest row per axis_name
    for row in rows:
        if row.axis_name and row.axis_name not in seen:
            seen.add(row.axis_name)
            result[row.axis_name] = AxisVerdict(
                axis_name=row.axis_name,
                label=row.label,
                label_index=row.label_index,
                p_top=row.p_top,
                p_critical=row.p_critical,
                p_danger=row.p_danger,
                escalated=bool(row.escalated),
                escalated_to=row.escalated_to,
                model_version=row.model_version,
                scored_at=row.scored_at.isoformat() if row.scored_at else None,
            )
    return result


def _build_trust_gate(url: Optional[str], name: Optional[str],
                       labels: dict[str, str]) -> TrustGateInfo:
    gate = trust_gate(url, name, labels)
    return TrustGateInfo(
        original_overall_risk=gate.get("original_overall_risk"),
        published_overall_risk=gate.get("published_overall_risk"),
        capped=bool(gate.get("capped")),
        trusted=bool(gate.get("trusted")),
        trust_basis=gate.get("trust_basis"),
        masquerade_flag=bool(gate.get("masquerade_flag")),
        display_label=gate.get("display_label", "Automated heuristic assessment"),
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk-tier-verdict/{server_id}",
    response_model=ServerRiskTierVerdict,
    summary="Get risk tier verdict for one MCP server",
)
def get_risk_tier_verdict(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerRiskTierVerdict:
    """Return the full risk-tier verdict for a server, including trust-gating."""
    # resolve registry record
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    # fetch all axis score rows for this server
    score_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    axes = _build_axis_dict(score_rows)

    # build label dict for trust gate
    labels: dict[str, str] = {}
    for row in score_rows:
        if row.axis_name and row.label:
            labels[row.axis_name] = row.label

    gate = _build_trust_gate(server.url, server.name, labels)

    return ServerRiskTierVerdict(
        server_id=server.server_id,
        name=server.name,
        registry_source=server.registry_source,
        url=server.url,
        description=server.description,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        confidence=server.confidence,
        trust_score=server.trust_score,
        last_assessed=(
            server.last_assessed.isoformat() if server.last_assessed else None
        ),
        axes=axes,
        trust_gate=gate,
    )


@router.get(
    "/risk-tier-verdict/{server_id}/axes",
    response_model=AxisVerdictResponse,
    summary="Get per-axis score rows for one MCP server",
)
def get_server_axes(
    server_id: str,
    db: Session = Depends(get_session),
) -> AxisVerdictResponse:
    """Return all axis score rows for the current model version of a server."""
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server '{server_id}'",
        )

    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
        .all()
    )

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server '{server_id}' with model version '{mv}'",
        )

    return AxisVerdictResponse(
        server_id=server_id,
        model_version=mv,
        axes=[AxisVerdict.model_validate(r) for r in rows],
    )


@router.get(
    "/risk-tier-verdict/summary",
    response_model=RiskTierSummary,
    summary="Global risk-tier distribution across all scored servers",
)
def get_risk_tier_summary(
    db: Session = Depends(get_session),
) -> RiskTierSummary:
    """Return aggregate risk-tier distribution."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    scored = (
        db.query(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .scalar()
        or 0
    )
    unscored = max(0, total - scored)

    tier_rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )

    tiers = [
        TierCount(
            tier=row.risk_tier or "UNKNOWN",
            count=row.cnt,
            percentage=round(row.cnt / total * 100, 2) if total > 0 else 0.0,
        )
        for row in tier_rows
    ]

    return RiskTierSummary(
        total_servers=total,
        scored_servers=scored,
        unscored_servers=unscored,
        tiers=tiers,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os
    import sys

    # add repo root so 'app' is importable when running this file directly
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _repo not in sys.path:
        sys.path.insert(0, _repo)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # seed test data
    sess = TS()
    # verified publisher: Stripe (github org in allow-list)
    sess.add(
        McpServerRegistry(
            server_id="srv_stripe",
            name="Stripe MCP",
            url="https://github.com/stripe/agent-toolkit",
            registry_source="github",
            verdict="HIGH",
            risk_tier="HIGH",
            confidence=0.9,
        )
    )
    # unknown server
    sess.add(
        McpServerRegistry(
            server_id="srv_unknown",
            name="Unknown MCP",
            url="https://example.com/unknown",
            registry_source="manual",
            verdict="MEDIUM",
            risk_tier="MEDIUM",
            confidence=0.6,
        )
    )
    mv = "v3.0_40974559"
    for ax, lbl in (
        ("overall_risk", "HIGH"),
        ("auth_strength", "STRONG"),
        ("capability_breadth", "BROAD"),
        ("data_sensitivity", "CRITICAL"),
        ("network_egress", "EXTERNAL"),
        ("maintainer_trust", "ESTABLISHED"),
        ("exploit_surface", "MODERATE"),
    ):
        sess.add(
            McpLlmAxisScore(
                server_id="srv_stripe",
                axis_name=ax,
                label=lbl,
                label_index=1,
                p_top=0.7,
                p_critical=0.1,
                p_danger=0.2,
                escalated=False,
                model_version=mv,
            )
        )
    sess.commit()
    sess.close()

    # build test app
    app = FastAPI()
    app.include_router(router)

    def _override():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    # import the app instance for dependency override
    from app.main import app as main_app

    main_app.dependency_overrides[get_session] = _override

    # use a separate test app that shares overrides
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)

    # --- happy path: Stripe server (verified publisher) ---
    r = client.get("/api/risk-tier-verdict/srv_stripe")
    assert r.status_code == 200, f"stripe verdict: {r.status_code} {r.text}"
    j = r.json()
    assert j["server_id"] == "srv_stripe"
    assert j["name"] == "Stripe MCP"
    assert j["trust_gate"] is not None
    # Stripe is in the verified org list → capped from HIGH to MEDIUM
    assert j["trust_gate"]["trusted"] is True, j["trust_gate"]
    assert j["trust_gate"]["published_overall_risk"] == "MEDIUM", j["trust_gate"]
    assert len(j["axes"]) == 7, f"expected 7 axes, got {len(j['axes'])}"

    # --- axes endpoint ---
    r2 = client.get("/api/risk-tier-verdict/srv_stripe/axes")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["model_version"] == mv
    assert len(j2["axes"]) >= 7, j2

    # --- unknown server ---
    r3 = client.get("/api/risk-tier-verdict/srv_unknown")
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["trust_gate"]["trusted"] is False

    # --- 404 ---
    r4 = client.get("/api/risk-tier-verdict/nosuchserver")
    assert r4.status_code == 404, r4.text

    # --- summary ---
    r5 = client.get("/api/risk-tier-verdict/summary")
    assert r5.status_code == 200, r5.text
    j5 = r5.json()
    assert j5["total_servers"] >= 2
    assert j5["scored_servers"] >= 1

    # --- axes 404 ---
    r6 = client.get("/api/risk-tier-verdict/nosuchserver/axes")
    assert r6.status_code == 404

    print("PASS")
