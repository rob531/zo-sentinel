"""server_verdict_detail_api.py -- GET /servers/{server_id}/verdict-detail.

Returns the full risk verdict detail for a single server: the 7 axes from
mcp_llm_axis_scores plus the derived risk_tier, merged with server metadata
from mcp_server_registry. Trust-gating is applied to the published overall_risk
so official publishers are not shown as false HIGH/CRITICAL.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(tags=["verdict-detail"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


# ===================== Pydantic models =====================

class AxisDetail(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False


class VerdictDetailResponse(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    url: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    scored_at: Optional[str] = None
    model_version: Optional[str] = None
    axes: list[AxisDetail]


# ===================== helpers =====================

def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _latest_scored_at(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(func.max(McpLlmAxisScore.scored_at))
        .where(McpLlmAxisScore.server_id == server_id)
    ).scalar()
    if row:
        return row.isoformat() if isinstance(row, datetime) else str(row)
    return None


# ===================== endpoint =====================

@router.get("/servers/{server_id}/verdict-detail", response_model=VerdictDetailResponse)
def get_verdict_detail(server_id: str, db: Session = Depends(get_session)) -> VerdictDetailResponse:
    """Full risk verdict detail for a single server: 7 axes + metadata + risk_tier."""
    # 1. fetch the 7 axis rows for the latest model version
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    # 2. build axes list and label map for trust gating
    axes: list[AxisDetail] = []
    labels: dict[str, str] = {}
    for r in rows:
        axes.append(AxisDetail(
            axis_name=r.axis_name,
            label=r.label,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=bool(r.escalated),
        ))
        if r.label:
            labels[r.axis_name] = r.label

    # 3. registry metadata
    reg = db.get(McpServerRegistry, server_id)
    server_name = reg.name if reg else None
    url = reg.url if reg else None
    verdict = reg.verdict if reg else None
    risk_tier = reg.risk_tier if reg else None

    # 4. apply trust gating (capped published overall_risk)
    gate = trust_gate(url, server_name, labels)

    # scored_at: use the latest axis scored_at
    scored_at = _latest_scored_at(db, server_id)

    return VerdictDetailResponse(
        server_id=server_id,
        server_name=server_name,
        url=url,
        verdict=gate.get("published_overall_risk") or verdict or labels.get("overall_risk"),
        risk_tier=risk_tier,
        scored_at=scored_at,
        model_version=mv,
        axes=axes,
    )


# ===================== self-test =====================

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # seed: one registry row + 7 axis rows (REQUIRED kwargs only per gate-8 rules)
    s = TS()
    s.add(McpServerRegistry(server_id="srv2", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            verdict="HIGH", risk_tier="MEDIUM"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"),
                                    ("auth_strength", "STRONG"),
                                    ("capability_breadth", "BROAD"),
                                    ("data_sensitivity", "CRITICAL"),
                                    ("network_egress", "EXTERNAL"),
                                    ("maintainer_trust", "ESTABLISHED"),
                                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv2", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # happy path: 7 axes present, risk_tier non-empty, scored_at ISO string
    r = c.get("/servers/srv2/verdict-detail")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    axis_names = {a["axis_name"] for a in j["axes"]}
    for ax in AXES:
        assert ax in axis_names, f"Missing axis {ax!r} in {axis_names}"
    assert isinstance(j["risk_tier"], str) and j["risk_tier"], f"risk_tier must be non-empty string, got {j['risk_tier']!r}"
    assert j["server_name"] == "Stripe MCP"
    assert j["model_version"] == "v3.0_40974559"
    # trust gating: Stripe is a verified publisher so verdict should be capped to MEDIUM
    assert j["verdict"] == "MEDIUM", f"Expected 'MEDIUM' (trust-gated), got {j['verdict']!r}"

    # 404 for unknown server
    r404 = c.get("/servers/nonexistent/verdict-detail")
    assert r404.status_code == 404, f"Expected 404, got {r404.status_code}"

    print("PASS")
