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
Router: GET /api/facets (authenticated), stale-while-revalidate TTL cache.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from clerk_m2m_auth import Principal, get_principal

router = APIRouter(prefix="/api", tags=["perspectives"])

REGISTRY_FACETS = ("risk_tier", "verdict", "registry_source")
AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")
TRUST_BANDS = ("0-25%", "25-50%", "50-75%", "75-100%")

_CACHE: dict = {"at": 0.0, "facets": None}
_REFRESH_LOCK = threading.Lock()
# 5 min fresh TTL + 1h stale-while-revalidate window: hot requests serve in
# <5ms without blocking on full-table GROUP BYs over 600k+ registry rows.
CACHE_TTL_SECS = 300
STALE_MAX_SECS = 3600


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
        # SQL-side banding (portable CASE; sqlite + Postgres). The prior
        # implementation pulled every trust_score into Python -- 65k rows per
        # cold call in prod (treewalk perf finding).
        lo_f, width = float(lo), (float(hi) - float(lo)) / 4.0
        ts = McpServerRegistry.trust_score
        band = case((ts < lo_f + width, TRUST_BANDS[0]),
                    (ts < lo_f + 2 * width, TRUST_BANDS[1]),
                    (ts < lo_f + 3 * width, TRUST_BANDS[2]),
                    else_=TRUST_BANDS[3])
        for b, c in db.execute(select(band, func.count())
                               .where(ts.is_not(None)).group_by(band)):
            band_counts[str(b)] = int(c)
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

    # vuln facet extension (P2 2026-07): kill-switch aware, best-effort --
    # mirrors app.main's optional-router mounting; an off switch or a missing
    # module leaves the facet universe untouched.
    try:
        from vuln_facet_extension import extend_facets
        extend_facets(db, facets)
    except Exception:
        pass
    return facets


def _trigger_bg_refresh(session_factory=None) -> None:
    """Refresh `_CACHE["facets"]` asynchronously under a non-blocking lock."""
    if not _REFRESH_LOCK.acquire(blocking=False):
        return

    def _worker() -> None:
        db = None
        try:
            if session_factory is None:
                from app.db import SessionLocal
                db = SessionLocal()
            else:
                db = session_factory()
            _CACHE["facets"] = compute_facets(db)
            _CACHE["at"] = time.time()
        except Exception:
            pass
        finally:
            if db is not None:
                db.close()
            _REFRESH_LOCK.release()

    threading.Thread(target=_worker, daemon=True).start()


def cached_facets(db: Session, *, session_factory=None) -> Dict[str, List[dict]]:
    """Stale-while-revalidate facet cache shared by `/facets` and perspective
    queries: warm within CACHE_TTL_SECS, instant stale + async refresh up to
    STALE_MAX_SECS, synchronous compute only on completely cold start or expiry."""
    now = time.time()
    age = now - float(_CACHE["at"] or 0.0)
    cached = _CACHE["facets"]
    if cached is not None:
        if age <= CACHE_TTL_SECS:
            return cached
        if age <= STALE_MAX_SECS:
            _trigger_bg_refresh(session_factory=session_factory)
            return cached
    _CACHE["facets"] = compute_facets(db)
    _CACHE["at"] = time.time()
    return _CACHE["facets"]


@router.get("/facets")
def get_facets(db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal)) -> dict:
    return {"facets": cached_facets(db), "cached_at": _CACHE["at"]}


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
    LocalS = sessionmaker(bind=eng)
    s = LocalS()
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

    # SWR test: aged cache between CACHE_TTL_SECS and STALE_MAX_SECS serves cached immediately
    _CACHE["facets"] = {"sentinel": [{"value": "cached", "count": 1}]}
    _CACHE["at"] = time.time() - (CACHE_TTL_SECS + 10)
    served = cached_facets(s, session_factory=LocalS)
    assert "sentinel" in served
    print("PASS")
