"""verdict_breakdown_api.py -- REAL per-server verdict endpoint (Tier-2 MVP).

Reads the SFT risk scores from Postgres (McpLlmAxisScore, ~65k rows) + registry
metadata (McpServerRegistry), and applies the trust-gating override so official
publishers (Stripe / Microsoft / Google ...) are NOT shown as false HIGH/CRITICAL.

Mounted automatically by app.main via _OPTIONAL_ROUTERS (exposes `router`).
This is the data-wired reference the fixed webapp_backend_fastapi recipe should now
produce: it imports the REAL app data layer (app.db / app.models) -- no inline stubs.
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

router = APIRouter(prefix="/api", tags=["verdict"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class AxisScore(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None


class Verdict(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    model_version: Optional[str] = None
    axes: Dict[str, AxisScore]
    model_overall_risk: Optional[str] = None       # raw model overall_risk label
    published_overall_risk: Optional[str] = None    # after trust_gating_override (capped)
    trusted: bool = False
    trust_basis: Optional[str] = None
    masquerade_flag: bool = False
    display_label: str = "Automated heuristic assessment"


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.get("/verdict/{server_id}", response_model=Verdict)
def get_verdict(server_id: str, db: Session = Depends(get_session)) -> Verdict:
    """Per-server verdict = its 7 axis rows for the latest model_version, with the
    trust-gating override applied to the published overall_risk."""
    mv = _latest_model_version(db, server_id)
    if mv is None:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None

    axes: Dict[str, AxisScore] = {}
    labels: Dict[str, str] = {}
    for r in rows:
        axes[r.axis_name] = AxisScore(axis_name=r.axis_name, label=r.label,
                                      label_index=r.label_index, p_top=r.p_top)
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(url, name, labels)
    return Verdict(
        server_id=server_id, name=name, url=url, model_version=mv, axes=axes,
        model_overall_risk=gate.get("original_overall_risk") or labels.get("overall_risk"),
        published_overall_risk=gate.get("published_overall_risk") or labels.get("overall_risk"),
        trusted=bool(gate.get("trusted")),
        trust_basis=gate.get("trust_basis"),
        masquerade_flag=bool(gate.get("masquerade_flag")),
        display_label=gate.get("display_label", "Automated heuristic assessment"),
    )


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
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
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
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
    r = c.get("/api/verdict/srv1"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["model_overall_risk"] == "HIGH", j
    assert j["published_overall_risk"] == "MEDIUM", j   # Stripe = verified -> capped
    assert j["trusted"] is True, j
    assert len(j["axes"]) == 7, j
    assert c.get("/api/verdict/nope").status_code == 404
    print("PASS")
