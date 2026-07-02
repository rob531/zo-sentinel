"""perspective_query_api.py -- the Perspectives read surface.

GET /api/perspectives/{id}/servers: compile the saved facet_filters into ONE
parameterized SQLAlchemy query over mcp_server_registry, with axis facets
applied as EXISTS subqueries on mcp_llm_axis_scores at the LATEST global
model_version. Paginated; returns {servers, total, facet_counts} where
facet_counts gives drill-down counts over the FILTERED set for the registry
facets. Fully deterministic -- zero LLM, zero string interpolation of values
(everything is bound parameters via the ORM).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

import json

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from facet_enum_service import (REGISTRY_FACETS, TRUST_BANDS, cached_facets,
                                latest_global_model_version, trust_band_for)
from perspective_model import get_perspective, validate_facet_filters
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api", tags=["perspectives"])

MAX_PAGE_SIZE = 100


def compile_filters(filters: dict, model_version: Optional[str]):
    """facet_filters -> list of SQLAlchemy predicates over McpServerRegistry.
    Pure: no session needed, unit-testable, values always bound."""
    preds = []
    for key, values in (filters or {}).items():
        vals = [str(v) for v in values]
        if key in REGISTRY_FACETS:
            preds.append(getattr(McpServerRegistry, key).in_(vals))
        elif key == "trust_band":
            # trust_band is derived; resolved post-query (see _band_filter).
            continue
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


def query_perspective_servers(db: Session, filters: dict, page: int = 1,
                              page_size: int = 25) -> Tuple[List[dict], int, Dict[str, list]]:
    """(servers, total, facet_counts) for a facet filter set."""
    mv = latest_global_model_version(db)
    preds = compile_filters(filters, mv)
    base = select(McpServerRegistry)
    if preds:
        base = base.where(and_(*preds))

    rows = list(db.execute(base).scalars())

    band_values = set((filters or {}).get("trust_band") or [])
    if band_values:
        lo, hi = db.execute(select(func.min(McpServerRegistry.trust_score),
                                   func.max(McpServerRegistry.trust_score))).one()
        rows = [r for r in rows
                if trust_band_for(r.trust_score, float(lo or 0), float(hi or 0)) in band_values]

    total = len(rows)
    start = max(0, (page - 1) * page_size)
    page_rows = rows[start:start + page_size]

    facet_counts: Dict[str, list] = {}
    for facet in REGISTRY_FACETS:
        counts: Dict[str, int] = {}
        for r in rows:
            v = getattr(r, facet)
            if v:
                counts[str(v)] = counts.get(str(v), 0) + 1
        facet_counts[facet] = sorted(
            ({"value": v, "count": c} for v, c in counts.items()),
            key=lambda d: -d["count"])

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
    against the live enums). Treewalk finding 2026-07-02: without this,
    clicking facets did nothing unless an admin had pre-saved a view."""
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
    print("PASS")
