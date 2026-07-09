"""perspective_query_api.py -- the Perspectives read surface (v1.2).

GET /api/perspectives/{id}/servers and /api/perspectives/adhoc/servers compile
facet_filters into parameterized SQL over mcp_server_registry, with axis facets
applied as EXISTS subqueries on mcp_llm_axis_scores at the LATEST global
model_version. Fully deterministic -- zero LLM, zero string interpolation of
values (everything is bound parameters via the ORM).

v1.2 (treewalk 2026-07-03 + council ruling): the v1.1 implementation
materialized EVERY matching ORM row per request (21k+ for a saved "High &
Critical" view; 80k unfiltered) and paginated/counted in Python -- the 10-20s
stall found live. Now:
  - total   = SQL count(*)
  - page    = ORDER BY server_id LIMIT/OFFSET (deterministic pagination)
  - trust_band filtering happens in SQL via the same CASE banding expression
    the facet service uses (identical boundary arithmetic -- keep in sync)
  - facet_counts are CONDITIONAL and cover EVERY facet group (registry,
    trust_band, and all 7 axes), computed SQL-side with standard faceted-nav
    "exclude own group" semantics: group X is counted with all active filters
    EXCEPT X applied, so OR-within-group options stay meaningfully clickable.
    An empty filter set is the global universe and is served from the shared
    TTL cache (facet_enum_service.cached_facets).
Covering indexes for the count queries live in migrations/versions/0006 (on
prod they are pre-created CONCURRENTLY via fly proxy; the migration is an
IF NOT EXISTS no-op there -- see the migration docstring).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, exists, false, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from facet_enum_service import (AXES, REGISTRY_FACETS, TRUST_BANDS,
                                cached_facets, latest_global_model_version)
from perspective_model import get_perspective, validate_facet_filters
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api", tags=["perspectives"])

MAX_PAGE_SIZE = 100


def _trust_bounds(db: Session) -> Tuple[float, float]:
    """Live [lo, hi] of trust_score; (0.0, 0.0) when the column is empty."""
    lo, hi = db.execute(select(func.min(McpServerRegistry.trust_score),
                               func.max(McpServerRegistry.trust_score))).one()
    return (float(lo) if lo is not None else 0.0,
            float(hi) if hi is not None else 0.0)


def _band_expr(lo: float, hi: float):
    """Portable SQL CASE mapping trust_score -> quartile band label.
    MUST stay arithmetically identical to facet_enum_service.trust_band_for
    (same equal-width banding, same inclusive edges), or filtered counts
    diverge from displayed counts."""
    width = (hi - lo) / 4.0
    ts = McpServerRegistry.trust_score
    return case((ts < lo + width, TRUST_BANDS[0]),
                (ts < lo + 2 * width, TRUST_BANDS[1]),
                (ts < lo + 3 * width, TRUST_BANDS[2]),
                else_=TRUST_BANDS[3])


def compile_filters(filters: dict, model_version: Optional[str],
                    exclude: Optional[str] = None,
                    trust_bounds: Optional[Tuple[float, float]] = None):
    """facet_filters -> list of SQLAlchemy predicates over McpServerRegistry.
    Values are ALWAYS bound parameters -- never interpolated. `exclude` drops
    one facet group (exclude-own-group conditional counting). trust_band is
    resolved in SQL when trust_bounds is supplied; with no live range it
    compiles to FALSE (no row can be in any band)."""
    preds = []
    for key, values in (filters or {}).items():
        if key == exclude:
            continue
        vals = [str(v) for v in values]
        if key in REGISTRY_FACETS:
            preds.append(getattr(McpServerRegistry, key).in_(vals))
        elif key == "trust_band":
            if trust_bounds and trust_bounds[1] > trust_bounds[0]:
                preds.append(and_(McpServerRegistry.trust_score.is_not(None),
                                  _band_expr(*trust_bounds).in_(vals)))
            else:
                preds.append(false())
        elif key.startswith("axis:") and model_version:
            axis = key[len("axis:"):]
            preds.append(exists(
                select(McpLlmAxisScore.id).where(and_(
                    McpLlmAxisScore.server_id == McpServerRegistry.server_id,
                    McpLlmAxisScore.axis_name == axis,
                    McpLlmAxisScore.label.in_(vals),
                    McpLlmAxisScore.model_version == model_version,
                ))))
    return preds


def conditional_facet_counts(db: Session, filters: dict,
                             model_version: Optional[str],
                             trust_bounds: Tuple[float, float]) -> Dict[str, list]:
    """Counts for EVERY facet group against the current selection
    (exclude-own-group semantics). Empty selection = the global universe,
    served from the shared TTL cache -- zero extra queries."""
    if not filters:
        return cached_facets(db)

    counts: Dict[str, list] = {}

    for group in REGISTRY_FACETS:
        preds = compile_filters(filters, model_version, exclude=group,
                                trust_bounds=trust_bounds)
        col = getattr(McpServerRegistry, group)
        q = select(col, func.count()).where(col.is_not(None))
        if preds:
            q = q.where(and_(*preds))
        rows = db.execute(q.group_by(col)).all()
        counts[group] = sorted(
            ({"value": str(v), "count": int(c)} for v, c in rows if v),
            key=lambda d: -d["count"])

    band_counts = {b: 0 for b in TRUST_BANDS}
    lo, hi = trust_bounds
    if hi > lo:
        preds = compile_filters(filters, model_version, exclude="trust_band",
                                trust_bounds=trust_bounds)
        band = _band_expr(lo, hi)
        q = (select(band, func.count())
             .where(McpServerRegistry.trust_score.is_not(None)))
        if preds:
            q = q.where(and_(*preds))
        for b, c in db.execute(q.group_by(band)):
            band_counts[str(b)] = int(c)
    counts["trust_band"] = [{"value": b, "count": band_counts[b]}
                            for b in TRUST_BANDS]

    if model_version:
        for axis in AXES:
            gkey = f"axis:{axis}"
            preds = compile_filters(filters, model_version, exclude=gkey,
                                    trust_bounds=trust_bounds)
            member = select(McpServerRegistry.server_id).where(
                McpServerRegistry.server_id == McpLlmAxisScore.server_id)
            if preds:
                member = member.where(and_(*preds))
            q = (select(McpLlmAxisScore.label,
                        func.count(func.distinct(McpLlmAxisScore.server_id)))
                 .where(McpLlmAxisScore.model_version == model_version,
                        McpLlmAxisScore.axis_name == axis,
                        McpLlmAxisScore.label.is_not(None),
                        exists(member))
                 .group_by(McpLlmAxisScore.label))
            counts[gkey] = sorted(
                ({"value": str(v), "count": int(c)} for v, c in db.execute(q)),
                key=lambda d: -d["count"])
    return counts


def query_membership(db: Session, filters: dict) -> Dict[str, str]:
    """Live {server_id: risk_tier} for a filter set -- tuple rows only, no ORM
    hydration, no facet counts. This is what trust-diff snapshots/diffs use
    (the v1.1 path materialized the full ORM result with page_size=1e9)."""
    mv = latest_global_model_version(db)
    bounds = _trust_bounds(db)
    preds = compile_filters(filters, mv, trust_bounds=bounds)
    q = select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
    if preds:
        q = q.where(and_(*preds))
    return {sid: (tier or "") for sid, tier in db.execute(q)}


def query_perspective_servers(db: Session, filters: dict, page: int = 1,
                              page_size: int = 25) -> Tuple[List[dict], int, Dict[str, list]]:
    """(servers, total, facet_counts) for a facet filter set -- all SQL-side."""
    mv = latest_global_model_version(db)
    bounds = _trust_bounds(db)
    preds = compile_filters(filters, mv, trust_bounds=bounds)
    where = and_(*preds) if preds else None

    count_q = select(func.count()).select_from(McpServerRegistry)
    if where is not None:
        count_q = count_q.where(where)
    total = int(db.execute(count_q).scalar_one())

    rows_q = select(McpServerRegistry)
    if where is not None:
        rows_q = rows_q.where(where)
    rows_q = (rows_q.order_by(McpServerRegistry.server_id)
              .limit(page_size).offset(max(0, (page - 1) * page_size)))
    page_rows = list(db.execute(rows_q).scalars())

    facet_counts = conditional_facet_counts(db, filters or {}, mv, bounds)

    servers = [{
        "server_id": r.server_id, "name": r.name, "url": r.url,
        "registry_source": r.registry_source, "risk_tier": r.risk_tier,
        "verdict": r.verdict, "trust_score": r.trust_score,
        "last_assessed": r.last_assessed.isoformat() if r.last_assessed else None,
    } for r in page_rows]
    return servers, total, facet_counts


@router.get("/perspectives/adhoc/servers")
def adhoc_servers(filters: str = Query("{}", description="facet_filters JSON"),
                  page: int = Query(1, ge=1),
                  page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
                  db: Session = Depends(get_session),
                  principal: Principal = Depends(get_principal)) -> dict:
    """Ad-hoc exploration for ANY authenticated user: same compile path as a
    saved perspective, but the filters come from the request (validated
    against the live enums). An empty filter set is now a legal, cheap
    "browse everything" page (treewalk finding 2026-07-03)."""
    try:
        f = json.loads(filters)
    except Exception:
        raise HTTPException(status_code=400, detail="filters must be valid JSON")
    if f:
        ok, why = validate_facet_filters(f, cached_facets(db))
        if not ok:
            raise HTTPException(status_code=400, detail=why)
    servers, total, facet_counts = query_perspective_servers(
        db, f, page=page, page_size=page_size)
    return {"filters": f, "servers": servers, "total": total, "page": page,
            "page_size": page_size, "facet_counts": facet_counts}


@router.get("/perspectives/{perspective_id}/servers")
def perspective_servers(perspective_id: str,
                        page: int = Query(1, ge=1),
                        page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
                        db: Session = Depends(get_session),
                        principal: Principal = Depends(get_principal)) -> dict:
    p = get_perspective(db, perspective_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Perspective not found")
    servers, total, facet_counts = query_perspective_servers(
        db, p.facet_filters or {}, page=page, page_size=page_size)
    return {"perspective": {"id": p.id, "name": p.name,
                            "facet_filters": p.facet_filters},
            "servers": servers, "total": total, "page": page,
            "page_size": page_size, "facet_counts": facet_counts}


if __name__ == "__main__":
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
        R(server_id="s1", name="weak-github", risk_tier="HIGH", verdict="HIGH",
          registry_source="github", trust_score=10.0),
        R(server_id="s2", name="strong-npm", risk_tier="LOW", verdict="LOW",
          registry_source="npm", trust_score=90.0),
        A(id=1, server_id="s1", axis_name="auth_strength", label="WEAK", model_version="v3"),
        A(id=2, server_id="s2", axis_name="auth_strength", label="STRONG", model_version="v3"),
    ])
    s.commit()

    # compile: both predicate classes present, values bound (no interpolation)
    preds = compile_filters({"risk_tier": ["HIGH"], "axis:auth_strength": ["WEAK"]}, "v3")
    assert len(preds) == 2
    compiled = str(select(McpServerRegistry).where(and_(*preds)))
    assert "IN (" in compiled and "EXISTS" in compiled.upper()
    assert "WEAK" not in compiled and "HIGH" not in compiled, \
        "values must be bound parameters, never inlined"

    servers, total, fc = query_perspective_servers(
        s, {"risk_tier": ["HIGH"], "axis:auth_strength": ["WEAK"]})
    assert total == 1 and servers[0]["server_id"] == "s1"
    assert fc["registry_source"] == [{"value": "github", "count": 1}]

    # v1.2: exclude-own-group semantics -- filtering a group must NOT zero out
    # its sibling options (OR-within-group stays clickable).
    _, total_g, fc_g = query_perspective_servers(s, {"registry_source": ["github"]})
    assert total_g == 1
    assert {d["value"]: d["count"] for d in fc_g["registry_source"]} == \
        {"github": 1, "npm": 1}, fc_g["registry_source"]
    # ...while OTHER groups are counted against the selection:
    assert {d["value"]: d["count"] for d in fc_g["risk_tier"]} == {"HIGH": 1}

    # v1.2: axis facet counts are conditional too (v1.1 shipped global only).
    _, _, fc_h = query_perspective_servers(s, {"risk_tier": ["HIGH"]})
    assert {d["value"]: d["count"] for d in fc_h["axis:auth_strength"]} == \
        {"WEAK": 1}, fc_h.get("axis:auth_strength")

    # v1.2: SQL pagination is deterministic (ORDER BY server_id).
    p1, tot, _ = query_perspective_servers(s, {}, page=1, page_size=1)
    p2, _, _ = query_perspective_servers(s, {}, page=2, page_size=1)
    assert tot == 2 and p1[0]["server_id"] == "s1" and p2[0]["server_id"] == "s2"

    # v1.2: empty filter set serves the cached global universe (same shape).
    _, _, fc_all = query_perspective_servers(s, {})
    assert "trust_band" in fc_all and "axis:auth_strength" in fc_all

    # v1.2: trust_band filtering happens in SQL and matches the band math.
    hi_only, hi_total, _ = query_perspective_servers(s, {"trust_band": ["75-100%"]})
    assert hi_total == 1 and hi_only[0]["server_id"] == "s2", (hi_total, hi_only)

    # v1.2: membership helper -- tuple path, no ORM, same filter semantics.
    m = query_membership(s, {"risk_tier": ["HIGH"]})
    assert m == {"s1": "HIGH"}, m
    print("PASS")
