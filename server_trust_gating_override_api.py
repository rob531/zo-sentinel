"""server_trust_gating_override_api.py -- trust-gating verdict endpoint.

Reads mcp_server_registry metadata (url, name) and mcp_llm_axis_scores, then
calls trust_gating_override.trust_gate(url, name, {axis_name: label}) for each
of the 7 axes, returning the derived published_overall_risk and trusted boolean
per axis plus the aggregate verdict.

Mounted automatically by app.main via _OPTIONAL_ROUTERS.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(tags=["trust-gating"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisTrust(BaseModel):
    label: Optional[str] = None
    published_overall_risk: Optional[str] = None
    trusted: bool = False


class TrustGatingResponse(BaseModel):
    server_id: str
    url: Optional[str] = None
    name: Optional[str] = None
    axes: Dict[str, AxisTrust]
    overall_trusted: bool
    verdict: str


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/servers/{server_id}/trust-gating", response_model=TrustGatingResponse)
def get_server_trust_gating(server_id: str, db: Session = Depends(get_session)) -> TrustGatingResponse:
    """Per-server trust-gating verdict = trust_gate result for each of the 7 axes.

    Returns the LLM label plus the trust_gate derived verdict (published_overall_risk,
    trusted) for each axis. overall_trusted is True only if ALL axes have trusted=True.
    """
    reg = db.get(McpServerRegistry, server_id)
    if reg is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    url = reg.url
    name = reg.name

    axes: Dict[str, AxisTrust] = {}
    all_labels: Dict[str, str] = {}
    for r in rows:
        all_labels[r.axis_name] = r.label or ""

    # Call trust_gate once with all labels to get overall verdict
    gate = trust_gate(url, name, all_labels)

    for r in rows:
        axis_gate = trust_gate(url, name, {r.axis_name: r.label or ""})
        axes[r.axis_name] = AxisTrust(
            label=r.label,
            published_overall_risk=axis_gate.get("published_overall_risk"),
            trusted=bool(axis_gate.get("trusted")),
        )

    overall_trusted = all(a.trusted for a in axes.values())
    verdict = gate.get("display_label", "Automated heuristic assessment")

    return TrustGatingResponse(
        server_id=server_id,
        url=url,
        name=name,
        axes=axes,
        overall_trusted=overall_trusted,
        verdict=verdict,
    )


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

    # Seed: verified publisher (Stripe) -> all axes trusted
    s = TS()
    s.add(McpServerRegistry(server_id="srv_stripe", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    for i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv_stripe", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))

    # Seed: unverified publisher -> not trusted
    s.add(McpServerRegistry(server_id="srv_unknown", name="Unknown MCP",
                            url="https://example.com/unknown"))
    for i, (ax, lbl) in enumerate((("overall_risk", "CRITICAL"), ("auth_strength", "WEAK"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "HIGH"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "UNKNOWN"),
                    ("exploit_surface", "HIGH")), start=100):
        s.add(McpLlmAxisScore(id=i, server_id="srv_unknown", axis_name=ax, label=lbl,
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

    # Happy path: verified publisher (Stripe) is trusted
    r = c.get("/servers/srv_stripe/trust-gating")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["server_id"] == "srv_stripe"
    assert j["url"] == "https://github.com/stripe/agent-toolkit"
    assert j["name"] == "Stripe MCP"
    assert len(j["axes"]) == 7, f"Expected 7 axes, got {len(j['axes'])}"
    for ax in ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
               "network_egress", "maintainer_trust", "exploit_surface"):
        assert ax in j["axes"], f"Missing axis: {ax}"
    assert j["overall_trusted"] is True, j
    assert j["verdict"] is not None

    # Unverified publisher is not trusted
    r2 = c.get("/servers/srv_unknown/trust-gating")
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["overall_trusted"] is False, j2

    # 404 for unknown server
    r3 = c.get("/servers/nope/trust-gating")
    assert r3.status_code == 404, r3.text

    print("PASS")
