"""facet_enum_service.py -- the deterministic facet universe for Perspectives.

v1.1 "Perspectives" foundation (PRODUCT_SPEC Appendix F; council 2026-06-27,
FATHER ruling). Facets are derived ONLY from real columns:
  - mcp_server_registry: risk_tier, verdict, registry_source, trust_score
    (equal-width quartile bands over the live min/max -> "trust_band")
  - mcp_llm_axis_scores: (axis_name, label) pairs for the 7 axes, filtered to
    the LATEST global model_version -> facet keys "axis:<axis_name>"
No invented facets (hosting-model / data-residency are OUT until real columns
exist). Zero per-query LLM. Imports the REAL app data layer (app.db /
app.models) -- no stubs, no write_service round-trips.

Pure business fn: compute_facets(db) -> {facet_key: [{"value","count"}]}.
Router: GET /api/facets (authenticated), 60s in-process TTL cache.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api", tags=["perspectives"])

REGISTRY_FACETS = ("risk_tier", "verdict", "registry_source")
AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")
TRUST_BANDS = ("0-25%", "25-50%", "50-75%", "75-100%")

_CACHE: dict = {"at": 0.0, "facets": None}
CACHE_TTL_SECS = 60


def latest_global_model_version(db: Session) -> Optional[str]:
    """The production-latest model_version across the whole score table --
    same ordering rule verdict_breakdown_api uses per server."""
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def trust_band_for(score: Optional[float], lo: float, hi: float) -> Optional[str]:
    """Equal-width quartile band for a trust_score given the live [lo, hi]
    range. Deterministic and portable (no DB percentile functions)."""
    if score is None or hi <= lo:
        return None
    frac = (score - lo) / (hi - lo)
    idx = min(3, max(0, int(frac * 4)))
    return TRUST_BANDS[idx]


def compute_facets(db: Session) -> Dict[str, List[dict]]:
    """The full facet universe with counts. One bounded aggregate per facet."""
    facets: Dict[str, List[dict]] = {}

    for col_name in REGISTRY_FACETS:
        col = getattr(McpServerRegistry, col_name)
        rows = db.execute(
            select(col, func.count()).where(col.is_not(None)).group_by(col)
        ).all()
        facets[col_name] = sorted(
            ({"value": str(v), "count": int(c)} for v, c in rows if v),
            key=lambda d: -d["count"])

    lo, hi = db.execute(
        select(func.min(McpServerRegistry.trust_score),
               func.max(McpServerRegistry.trust_score))
    ).one()
    band_counts = {b: 0 for b in TRUST_BANDS}
    if lo is not None and hi is not None and hi > lo:
        for (score,) in db.execute(
                select(McpServerRegistry.trust_score)
                .where(McpServerRegistry.trust_score.is_not(None))):
            b = trust_band_for(score, float(lo), float(hi))
            if b:
                band_counts[b] += 1
    facets["trust_band"] = [{"value": b, "count": band_counts[b]} for b in TRUST_BANDS]

    mv = latest_global_model_version(db)
    if mv:
        rows = db.execute(
            select(McpLlmAxisScore.axis_name, McpLlmAxisScore.label, func.count())
            .where(McpLlmAxisScore.model_version == mv,
                   McpLlmAxisScore.label.is_not(None))
            .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        ).all()
        for axis, label, count in rows:
            facets.setdefault(f"axis:{axis}", []).append(
                {"value": str(label), "count": int(count)})
        for k in list(facets):
            if k.startswith("axis:"):
                facets[k].sort(key=lambda d: -d["count"])
    return facets


@router.get("/facets")
def get_facets(db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal)) -> dict:
    now = time.time()
    if _CACHE["facets"] is None or now - _CACHE["at"] > CACHE_TTL_SECS:
        _CACHE["facets"] = compute_facets(db)
        _CACHE["at"] = now
    return {"facets": _CACHE["facets"], "cached_at": _CACHE["at"]}


if __name__ == "__main__":
    # Self-test on an in-memory sqlite with heterogeneous sample rows.
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpLlmAxisScore as A, McpServerRegistry as R
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        R(server_id="s1", risk_tier="HIGH", verdict="HIGH", registry_source="github", trust_score=10.0),
        R(server_id="s2", risk_tier="LOW", verdict="LOW", registry_source="npm", trust_score=90.0),
        R(server_id="s3", risk_tier="HIGH", verdict="HIGH", registry_source="github", trust_score=55.0),
        A(id=1, server_id="s1", axis_name="auth_strength", label="WEAK", model_version="v3"),
        A(id=2, server_id="s2", axis_name="auth_strength", label="STRONG", model_version="v3"),
    ])
    s.commit()
    f = compute_facets(s)
    assert {d["value"] for d in f["risk_tier"]} == {"HIGH", "LOW"}
    assert f["risk_tier"][0] == {"value": "HIGH", "count": 2}
    assert "axis:auth_strength" in f, "axis facets must be keyed axis:<name>"
    assert sum(d["count"] for d in f["registry_source"]) == 3
    assert sum(d["count"] for d in f["trust_band"]) == 3
    print("PASS")
