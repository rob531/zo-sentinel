"""server_compare_api.py -- side-by-side risk comparison of multiple MCP servers.

Mirrors verdict_breakdown_api.py (the exemplar): imports the REAL data layer, applies
the trust-gating override, reuses the Clerk auth + lookup charging, and ships a runnable
__main__ self-test. published_overall_risk + trusted are DERIVED from trust_gate() (they
are NOT stored DB axes). Mounted via app.main _OPTIONAL_ROUTERS.

GET /api/compare?ids=a,b,c  (2-8 ids) -> per-server 7-axis matrix + trust-gated overall.
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
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None


class CompareServer(BaseModel):
    server_id: str
    found: bool = True
    name: Optional[str] = None
    url: Optional[str] = None
    model_version: Optional[str] = None
    model_overall_risk: Optional[str] = None       # raw model overall_risk label
    published_overall_risk: Optional[str] = None    # trust_gate-capped (DERIVED, not stored)
    trusted: bool = False
    axes: Dict[str, AxisCell] = {}


class CompareResponse(BaseModel):
    servers: List[CompareServer]
    axes_order: List[str] = list(AXES)


@router.get("/compare", response_model=CompareResponse)
def compare_servers(ids: str = Query(..., description="comma-separated server_ids (2-8)"),
                    db: Session = Depends(get_session),
                    principal: Principal = Depends(get_principal)) -> CompareResponse:
    """Compare 2-8 servers side by side. Bounded (iterates the ids; no full-table scans)."""
    server_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not (2 <= len(server_ids) <= 8):
        raise HTTPException(status_code=400, detail="Provide between 2 and 8 server_ids")
    charge_lookup(db, principal)   # a comparison counts as one lookup

    out: List[CompareServer] = []
    for sid in server_ids:
        mv = _latest_model_version(db, sid)
        if mv is None:
            out.append(CompareServer(server_id=sid, found=False))
            continue
        rows = db.execute(select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == sid,
            McpLlmAxisScore.model_version == mv)).scalars().all()
        reg = db.get(McpServerRegistry, sid)
        url = reg.url if reg else None
        name = reg.name if reg else None
        labels = {r.axis_name: r.label for r in rows if r.label}
        axes = {r.axis_name: AxisCell(label=r.label, label_index=r.label_index, p_top=r.p_top)
                for r in rows}
        gate = trust_gate(url, name, labels)   # returns a dict; unpack it
        out.append(CompareServer(
            server_id=sid, found=True, name=name, url=url, model_version=mv,
            model_overall_risk=gate.get("original_overall_risk") or labels.get("overall_risk"),
            published_overall_risk=gate.get("published_overall_risk") or labels.get("overall_risk"),
            trusted=bool(gate.get("trusted")),
            axes=axes))
    return CompareResponse(servers=out)


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
    _i = 1
    for _sid, _name in (("s1", "Server One"), ("s2", "Server Two")):
        s.add(McpServerRegistry(server_id=_sid, name=_name,
                                url=f"https://github.com/example/{_sid}"))
        for _ax in AXES:
            s.add(McpLlmAxisScore(id=_i, server_id=_sid, axis_name=_ax,
                                  label=("HIGH" if _ax == "overall_risk" else "MODERATE"),
                                  label_index=1, p_top=0.7, model_version="v3.0_test"))
            _i += 1
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
    r = c.get("/api/compare?ids=s1,s2"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["servers"]) == 2, j
    for _sv in j["servers"]:
        assert _sv["found"] is True, _sv
        assert len(_sv["axes"]) == 7, _sv
        assert _sv["published_overall_risk"], _sv
    assert len(j["axes_order"]) == 7, j
    assert c.get("/api/compare?ids=s1").status_code == 400      # needs >=2
    assert c.get("/api/compare?ids=a,b,c,d,e,f,g,h,i").status_code == 400  # >8
    print("PASS")
