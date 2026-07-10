"""server_risk_comparison_api.py -- side-by-side risk comparison of two MCP servers.

GET /servers/compare?left=<server_id>&right=<server_id> -> per-axis labels,
p_critical, p_top for each server, plus delta (right - left) for p_top and p_critical.
Also returns each server's risk_tier from mcp_server_registry.
Uses the REAL app data layer (app.db / app.models); no inline stubs.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from trust_gating_override import trust_gate
from verdict_breakdown_api import (
    get_principal, charge_lookup, Principal, _latest_model_version, AXES,
)

router = APIRouter(prefix="/api", tags=["compare"])


class AxisCell(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_critical: Optional[float] = None
    p_top: Optional[float] = None


class ComparedServer(BaseModel):
    server_id: str
    found: bool = True
    name: Optional[str] = None
    url: Optional[str] = None
    risk_tier: Optional[str] = None
    model_version: Optional[str] = None
    model_overall_risk: Optional[str] = None
    published_overall_risk: Optional[str] = None
    trusted: bool = False
    axes: List[AxisCell] = []


class DeltaCell(BaseModel):
    axis_name: str
    p_critical_delta: Optional[float] = None
    p_top_delta: Optional[float] = None


class CompareResponse(BaseModel):
    left: ComparedServer
    right: ComparedServer
    axes_order: List[str] = list(AXES)
    deltas: List[DeltaCell] = []


def _build_compared_server(db: Session, sid: str, mv: Optional[str]) -> ComparedServer:
    reg = db.get(McpServerRegistry, sid)
    name = reg.name if reg else None
    url = reg.url if reg else None
    risk_tier = reg.risk_tier if reg else None

    labels: Dict[str, str] = {}
    axes: List[AxisCell] = []
    if mv:
        rows = db.execute(select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == sid,
            McpLlmAxisScore.model_version == mv)).scalars().all()
        for r in rows:
            if r.label:
                labels[r.axis_name] = r.label
            axes.append(AxisCell(
                axis_name=r.axis_name,
                label=r.label,
                label_index=r.label_index,
                p_critical=r.p_critical,
                p_top=r.p_top))
    else:
        axes = [AxisCell(axis_name=ax) for ax in AXES]

    gate = trust_gate(url, name, labels)
    return ComparedServer(
        server_id=sid,
        found=True,
        name=name,
        url=url,
        risk_tier=risk_tier,
        model_version=mv,
        model_overall_risk=gate.get("original_overall_risk") or labels.get("overall_risk"),
        published_overall_risk=gate.get("published_overall_risk") or labels.get("overall_risk"),
        trusted=bool(gate.get("trusted")),
        axes=axes,
    )


def _not_found_server(sid: str) -> ComparedServer:
    return ComparedServer(server_id=sid, found=False)


@router.get("/servers/compare", response_model=CompareResponse)
def compare_servers(
    left: str = Query(..., description="Left server_id"),
    right: str = Query(..., description="Right server_id"),
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> CompareResponse:
    """Side-by-side risk comparison of two servers. Returns per-axis labels,
    p_critical, p_top, and delta (right - left) for the probability fields.
    Also includes risk_tier from mcp_server_registry for each server."""
    left = left.strip()
    right = right.strip()
    if not left or not right:
        raise HTTPException(status_code=400, detail="Both 'left' and 'right' server_ids are required")
    charge_lookup(db, principal)   # a comparison counts as one lookup

    left_mv = _latest_model_version(db, left)
    right_mv = _latest_model_version(db, right)

    left_srv = _build_compared_server(db, left, left_mv) if left_mv else _not_found_server(left)
    right_srv = _build_compared_server(db, right, right_mv) if right_mv else _not_found_server(right)

    # Build axes index keyed by axis_name for delta computation
    left_axes_map: Dict[str, AxisCell] = {ax.axis_name: ax for ax in left_srv.axes}
    right_axes_map: Dict[str, AxisCell] = {ax.axis_name: ax for ax in right_srv.axes}

    deltas: List[DeltaCell] = []
    for ax in AXES:
        lp = left_axes_map.get(ax)
        rp = right_axes_map.get(ax)
        deltas.append(DeltaCell(
            axis_name=ax,
            p_critical_delta=(rp.p_critical - lp.p_critical) if (lp and rp and lp.p_critical is not None and rp.p_critical is not None) else None,
            p_top_delta=(rp.p_top - lp.p_top) if (lp and rp and lp.p_top is not None and rp.p_top is not None) else None,
        ))

    return CompareResponse(
        left=left_srv,
        right=right_srv,
        axes_order=list(AXES),
        deltas=deltas,
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

    # Seed left server (s1) with known p_top=0.8, p_critical=0.3
    s.add(McpServerRegistry(server_id="s1", name="Left Server",
                            url="https://github.com/example/s1", risk_tier="HIGH"))
    for i, ax in enumerate(AXES, start=1):
        s.add(McpLlmAxisScore(
            id=i, server_id="s1", axis_name=ax,
            label=("HIGH" if ax == "overall_risk" else "MODERATE"),
            model_version="v3.0_40974559",
            p_top=0.8, p_critical=0.3,
        ))

    # Seed right server (s2) with known p_top=0.5, p_critical=0.1
    for i, ax in enumerate(AXES, start=len(AXES) + 1):
        s.add(McpLlmAxisScore(
            id=i, server_id="s2", axis_name=ax,
            label=("MEDIUM" if ax == "overall_risk" else "MODERATE"),
            model_version="v3.0_40974559",
            p_top=0.5, p_critical=0.1,
        ))
    s.add(McpServerRegistry(server_id="s2", name="Right Server",
                            url="https://github.com/example/s2", risk_tier="MEDIUM"))

    # Seed a server with no scores (should appear as not found)
    s.add(McpServerRegistry(server_id="s3", name="No Scores",
                            url="https://github.com/example/s3", risk_tier=None))

    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="t", role="admin")
    c = TestClient(app)

    # Happy path: compare two servers with scores
    r = c.get("/api/servers/compare?left=s1&right=s2")
    assert r.status_code == 200, r.text
    j = r.json()

    # Both server IDs present
    assert j["left"]["server_id"] == "s1", j
    assert j["right"]["server_id"] == "s2", j

    # Both found
    assert j["left"]["found"] is True, j
    assert j["right"]["found"] is True, j

    # All 7 axes each
    assert len(j["left"]["axes"]) == 7, j
    assert len(j["right"]["axes"]) == 7, j

    # risk_tier present
    assert j["left"]["risk_tier"] == "HIGH", j
    assert j["right"]["risk_tier"] == "MEDIUM", j

    # deltas present for all 7 axes
    assert len(j["deltas"]) == 7, j
    for d in j["deltas"]:
        assert d["axis_name"] in AXES, d

    # p_top delta = 0.5 - 0.8 = -0.3 (right minus left)
    overall_delta = next((d for d in j["deltas"] if d["axis_name"] == "overall_risk"), None)
    assert overall_delta is not None, j
    assert abs(overall_delta["p_top_delta"] - (-0.3)) < 1e-9, overall_delta
    assert abs(overall_delta["p_critical_delta"] - (-0.2)) < 1e-9, overall_delta

    # axes_order has 7 entries
    assert len(j["axes_order"]) == 7, j

    # Missing left server: right has scores, left does not
    r2 = c.get("/api/servers/compare?left=s3&right=s2")
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["left"]["found"] is False, j2
    assert j2["right"]["found"] is True, j2

    # Empty left param -> 400
    r3 = c.get("/api/servers/compare?left=&right=s2")
    assert r3.status_code == 400, r3.text

    print("PASS")
