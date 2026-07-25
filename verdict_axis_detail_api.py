"""verdict_axis_detail_api.py -- per-server 7-axis detail endpoint (Tier-2 MVP).

GET /verdict/{server_id}/detail
Reads all 7 risk axes from mcp_llm_axis_scores via the SQLAlchemy session
(real data layer -- no write_service for reads).  Applies trust_gating_override
so official publishers are not shown as false HIGH/CRITICAL.  Computes risk_tier
via a rule-override (CRITICAL axis forces the tier).

Mounted by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(tags=["verdict"])

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


# ---- Pydantic models ---------------------------------------------------------

class AxisDetail(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    probs: Optional[Dict] = None


class DerivedFields(BaseModel):
    published_overall_risk: Optional[str] = None
    trusted: bool = False


class VerdictAxisDetailResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    axes: Dict[str, AxisDetail]
    derived: DerivedFields
    risk_tier: str
    scored_at: Optional[datetime] = None


# ---- Risk-tier computation ---------------------------------------------------

def _compute_risk_tier(labels: Dict[str, str]) -> str:
    """Rule-override: any CRITICAL axis forces the tier to CRITICAL.
    Otherwise fall back to published overall_risk."""
    if "CRITICAL" in {v.upper() for v in labels.values()}:
        return "CRITICAL"
    ov = (labels.get("overall_risk") or "").upper()
    if ov == "CRITICAL":
        return "CRITICAL"
    if ov == "HIGH":
        return "HIGH"
    if ov == "MEDIUM":
        return "MEDIUM"
    if ov == "LOW":
        return "LOW"
    return "UNKNOWN"


# ---- Endpoint -----------------------------------------------------------------

def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = (
        db.execute(
            select(McpLlmAxisScore.model_version)
            .where(McpLlmAxisScore.server_id == server_id)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    return row


@router.get("/verdict/{server_id}/detail", response_model=VerdictAxisDetailResponse)
def get_verdict_axis_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> VerdictAxisDetailResponse:
    """Return the full 7-axis breakdown for a server, with trust-gating applied
    and a rule-override risk tier."""
    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None
    verdict = reg.verdict if reg else None

    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = (
        db.execute(
            select(McpLlmAxisScore).where(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.model_version == mv,
            )
        )
        .scalars()
        .all()
    )

    axes: Dict[str, AxisDetail] = {}
    labels: Dict[str, str] = {}
    scored_at: Optional[datetime] = None

    for r in rows:
        axes[r.axis_name] = AxisDetail(
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            probs=r.probs,
        )
        if r.label:
            labels[r.axis_name] = r.label
        if r.scored_at and scored_at is None:
            scored_at = r.scored_at

    gate = trust_gate(url, name, labels)

    return VerdictAxisDetailResponse(
        server_id=server_id,
        name=name,
        verdict=verdict,
        axes=axes,
        derived=DerivedFields(
            published_overall_risk=gate.get("published_overall_risk"),
            trusted=bool(gate.get("trusted")),
        ),
        risk_tier=_compute_risk_tier(labels),
        scored_at=scored_at,
    )


# ---- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed: one server with all 7 axes (mirrors verdict_breakdown_api.py seed pattern)
    sess = TS()
    sess.add(
        McpServerRegistry(
            server_id="srv1",
            name="Stripe MCP",
            url="https://github.com/stripe/agent-toolkit",
            verdict="PASS",
        )
    )
    for _i, (ax, lbl) in enumerate(
        (
            ("overall_risk", "HIGH"),
            ("auth_strength", "STRONG"),
            ("capability_breadth", "BROAD"),
            ("data_sensitivity", "CRITICAL"),
            ("network_egress", "EXTERNAL"),
            ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "MODERATE"),
        ),
        start=1,
    ):
        sess.add(
            McpLlmAxisScore(
                id=_i,
                server_id="srv1",
                axis_name=ax,
                label=lbl,
                model_version="v3.0_40974559",
            )
        )
    sess.commit()
    sess.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)

    # Happy path: all 7 axes present, CRITICAL data_sensitivity -> tier=CRITICAL
    r = client.get("/verdict/srv1/detail")
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["axes"].keys()) == set(AXES), f"Expected 7 axes, got {list(j['axes'].keys())}"
    assert isinstance(j["risk_tier"], str) and j["risk_tier"] != "", j
    assert j["derived"]["trusted"] is True, j   # Stripe = verified publisher
    assert j["derived"]["published_overall_risk"] == "MEDIUM", j   # trust-gate cap
    assert j["scored_at"] is not None, j

    # Unknown server -> 404
    r2 = client.get("/verdict/unknown-server/detail")
    assert r2.status_code == 404, r2.text

    print("PASS")
