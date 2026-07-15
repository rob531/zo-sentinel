"""Risk tier comparison API -- compare risk tiers across multiple servers."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/risk-tier", tags=["risk"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


class ServerRiskComparison(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    model_version: Optional[str] = None
    axes: dict
    published_overall_risk: Optional[str] = None
    trusted: bool = False
    trust_basis: Optional[str] = None


class AxisSummary(BaseModel):
    min_label: Optional[str] = None
    max_label: Optional[str] = None
    shared_axes: int
    total_axes: int


class RiskTierComparisonResponse(BaseModel):
    servers: List[ServerRiskComparison]
    axis_summary: AxisSummary
    model_versions: List[str]


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def _get_server_scores(db: Session, server_id: str) -> Optional[dict]:
    mv = _latest_model_version(db, server_id)
    if mv is None:
        return None

    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
        )
    ).scalars().all()

    if not rows:
        return None

    reg = db.get(McpServerRegistry, server_id)
    name = reg.name if reg else None
    url = reg.url if reg else None

    axes: dict = {}
    labels: dict = {}
    for r in rows:
        axes[r.axis_name] = {"label": r.label, "label_index": r.label_index, "p_top": r.p_top}
        if r.label:
            labels[r.axis_name] = r.label

    gate = trust_gate(url, name, labels)
    return {
        "server_id": server_id,
        "name": name,
        "url": url,
        "model_version": mv,
        "axes": axes,
        "published_overall_risk": gate.get("published_overall_risk") or labels.get("overall_risk"),
        "trusted": bool(gate.get("trusted")),
        "trust_basis": gate.get("trust_basis"),
    }


@router.get("/comparison", response_model=RiskTierComparisonResponse)
def get_risk_tier_comparison(
    server_ids: str,
    db: Session = Depends(get_session),
) -> RiskTierComparisonResponse:
    """Compare risk tiers across multiple servers by their overall_risk axis.

    Args:
        server_ids: comma-separated list of server_ids (e.g. "srv1,srv2,srv3")

    Returns:
        Per-server risk breakdown + axis summary comparing labels across servers.
    """
    ids = [s.strip() for s in server_ids.split(",") if s.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="At least one server_id is required")
    if len(ids) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 servers per comparison")

    servers_data: List[ServerRiskComparison] = []
    model_versions_set: set = set()
    all_axis_names: set = set()

    for sid in ids:
        data = _get_server_scores(db, sid)
        if data is None:
            continue
        servers_data.append(ServerRiskComparison(**data))
        if data["model_version"]:
            model_versions_set.add(data["model_version"])
        all_axis_names.update(data["axes"].keys())

    if not servers_data:
        raise HTTPException(status_code=404, detail="No servers found with risk tier data")

    shared_axes = 0
    if servers_data:
        first_axes = set(servers_data[0].axes.keys())
        for s in servers_data[1:]:
            first_axes &= set(s.axes.keys())
        shared_axes = len(first_axes)

    overall_risk_labels = [s.published_overall_risk for s in servers_data if s.published_overall_risk]
    min_label = min(overall_risk_labels) if overall_risk_labels else None
    max_label = max(overall_risk_labels) if overall_risk_labels else None

    return RiskTierComparisonResponse(
        servers=servers_data,
        axis_summary=AxisSummary(
            min_label=min_label,
            max_label=max_label,
            shared_axes=shared_axes,
            total_axes=len(all_axis_names),
        ),
        model_versions=sorted(model_versions_set),
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

    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    s.add(McpServerRegistry(server_id="srv2", name="Acme MCP",
                            url="https://github.com/acme/mcp-server"))
    s.add(McpServerRegistry(server_id="srv3", name="Evil Corp",
                            url="https://evilcorp.example.com/mcp"))
    for i, (sid, ax, lbl) in enumerate([
        ("srv1", "overall_risk", "HIGH"), ("srv1", "auth_strength", "STRONG"),
        ("srv2", "overall_risk", "MEDIUM"), ("srv2", "auth_strength", "WEAK"),
        ("srv3", "overall_risk", "CRITICAL"), ("srv3", "auth_strength", "WEAK"),
    ], start=1):
        s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    c = TestClient(app)

    # happy path: 3 servers
    r = c.get("/risk-tier/comparison?server_ids=srv1,srv2,srv3")
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["servers"]) == 3, j
    assert j["axis_summary"]["shared_axes"] >= 1, j
    assert "v3.0_40974559" in j["model_versions"], j

    # single server
    r2 = c.get("/risk-tier/comparison?server_ids=srv1")
    assert r2.status_code == 200, r2.text
    assert len(r2.json()["servers"]) == 1

    # mixed: some exist, some don't
    r3 = c.get("/risk-tier/comparison?server_ids=srv1,nope,srv2")
    assert r3.status_code == 200, r3.text
    assert len(r3.json()["servers"]) == 2

    # none found
    r4 = c.get("/risk-tier/comparison?server_ids=nope1,nope2")
    assert r4.status_code == 404, r4.text

    # empty server_ids
    r5 = c.get("/risk-tier/comparison?server_ids=")
    assert r5.status_code == 400, r5.text

    print("PASS")
