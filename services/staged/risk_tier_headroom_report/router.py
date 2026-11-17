# deps: sqlalchemy
"""risk_tier_headroom_report.py -- per-tier headroom to the tier boundary.

Reads the SFT overall_risk scores (mcp_llm_axis_scores) joined to the published
registry tier (mcp_server_registry) and reports, per risk tier, how close each
server sits to that tier's boundary -- i.e. the headroom to the next tier up
or down.

Two layers, mirroring axis_tier_distribution_report.py / verdict_breakdown_api.py:
  * pure layer -- tier_headroom(rows) -> dict (stdlib only: statistics, datetime).
  * data seam -- load_headroom_rows(db) reads the REAL app tables through the
    REAL app session (app.db.get_session) and app.models. No own engine, no
    writes, no network.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Hardcoded tier boundaries (p_top) -- never invented at runtime.
TIER_BOUNDARIES: Dict[str, float] = {
    "TRUSTED_GENERAL": 0.75,
    "TRUSTED_RESEARCH": 0.60,
    "ENTERPRISE_CONTROLLED": 0.45,
    "CAUTION_LIMITED": 0.30,
    "HIGH_RISK_ISOLATED": 0.15,
}

NEAR_BOUNDARY_WINDOW = 0.05


# ---------------------------------------------------------------- pure layer

def _coerce_p_top(row: Dict[str, Any]) -> Optional[float]:
    raw = row.get("p_top")
    if raw is None:
        return None
    try:
        p = float(raw)
    except (TypeError, ValueError):
        return None
    if p != p:  # NaN
        return None
    return min(1.0, max(0.0, p))


def tier_headroom(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Headroom to the tier boundary for every server in `rows`.

    rows: dicts with server_id, p_top, risk_tier and (optionally) name.
    Rows without a usable p_top or a tier are ignored. Returns
    {"tiers": {tier: {"count", "min_p_top", "max_p_top", "mean_p_top",
    "near_boundary": [{server_id, name, p_top, headroom}]}},
    "near_boundary_count": int, "boundary": {tier: threshold}}.
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tier = str(row.get("risk_tier") or "").strip().upper()
        sid = str(row.get("server_id") or "").strip()
        p = _coerce_p_top(row)
        if not tier or not sid or p is None:
            continue
        bucket = buckets.setdefault(tier, {"ps": [], "near_boundary": []})
        bucket["ps"].append(p)
        boundary = TIER_BOUNDARIES.get(tier)
        if boundary is None:
            continue
        headroom = abs(boundary - p)
        if headroom <= NEAR_BOUNDARY_WINDOW:
            bucket["near_boundary"].append({
                "server_id": sid,
                "name": row.get("name"),
                "p_top": p,
                "headroom": headroom,
            })

    tiers: Dict[str, Dict[str, Any]] = {}
    for tier, bucket in buckets.items():
        ps = bucket["ps"]
        near = sorted(bucket["near_boundary"], key=lambda e: (e["headroom"], e["server_id"]))
        tiers[tier] = {
            "count": len(ps),
            "min_p_top": min(ps),
            "max_p_top": max(ps),
            "mean_p_top": round(fmean(ps), 6),
            "near_boundary": near,
        }

    ordered = sorted(tiers, key=lambda t: (-TIER_BOUNDARIES.get(t, -1.0), t))
    tiers = {t: tiers[t] for t in ordered}

    return {
        "tiers": tiers,
        "near_boundary_count": sum(len(t["near_boundary"]) for t in tiers.values()),
        "boundary": dict(TIER_BOUNDARIES),
    }


# --------------------------------------------------------------- data seam

def load_headroom_rows(db: Session) -> List[Dict[str, Any]]:
    """Latest overall_risk score per server joined to its registry tier."""
    registry = {}
    for sid, name, tier, verdict, url in db.execute(
        select(McpServerRegistry.server_id,
               McpServerRegistry.name,
               McpServerRegistry.risk_tier,
               McpServerRegistry.verdict,
               McpServerRegistry.url)
    ).all():
        registry[sid] = {"name": name, "risk_tier": tier, "verdict": verdict, "url": url}

    latest: Dict[str, Dict[str, Any]] = {}
    score_rows = db.execute(
        select(McpLlmAxisScore.server_id,
               McpLlmAxisScore.p_top,
               McpLlmAxisScore.label,
               McpLlmAxisScore.scored_at,
               McpLlmAxisScore.id)
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
    ).all()
    for sid, p_top, label, scored_at, _rid in score_rows:
        if sid not in latest:
            latest[sid] = {"p_top": p_top, "label": label, "scored_at": scored_at}

    rows: List[Dict[str, Any]] = []
    for sid, info in registry.items():
        score = latest.get(sid)
        if score is None:
            continue
        rows.append({
            "server_id": sid,
            "name": info["name"],
            "risk_tier": info["risk_tier"],
            "verdict": info["verdict"],
            "url": info["url"],
            "p_top": score["p_top"],
            "label": score["label"],
            "scored_at": score["scored_at"],
        })
    return rows


def _resolve_session(db_session_or_callable: Any) -> Any:
    """Accept a Session, a sessionmaker, or a callable returning a Session."""
    candidate = db_session_or_callable
    if callable(candidate):
        candidate = candidate()
    return candidate


def build_report(db_session_or_callable: Any) -> Dict[str, Any]:
    """Build the headroom report from the real app tables (read-only).

    db_session_or_callable: an app.db session (or a callable returning one).
    """
    db = _resolve_session(db_session_or_callable)
    report = tier_headroom(load_headroom_rows(db))
    return {"generated_at": datetime.now(timezone.utc).isoformat(), **report}


@router.get("/risk-tier-headroom")
def risk_tier_headroom(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Per-tier headroom to the tier boundary for every scored server."""
    return build_report(db)


# --------------------------------------------------------------- self-test

def _self_test() -> int:
    # --- pure layer: 4 synthetic rows, 2 tiers ---
    rows = [
        {"server_id": "srv_a1", "name": "Alpha MCP", "risk_tier": "CAUTION_LIMITED",
         "p_top": 0.25, "label": "HIGH"},
        {"server_id": "srv_a2", "name": "Beta MCP", "risk_tier": "CAUTION_LIMITED",
         "p_top": 0.375, "label": "HIGH"},
        {"server_id": "srv_b1", "name": "Gamma MCP", "risk_tier": "ENTERPRISE_CONTROLLED",
         "p_top": 0.25, "label": "MEDIUM"},
        {"server_id": "srv_b2", "name": "Delta MCP", "risk_tier": "ENTERPRISE_CONTROLLED",
         "p_top": 0.5, "label": "MEDIUM"},
    ]
    rep = tier_headroom(rows)
    assert len(rep["tiers"]) == 2, rep
    caution = rep["tiers"]["CAUTION_LIMITED"]
    controlled = rep["tiers"]["ENTERPRISE_CONTROLLED"]
    assert caution["count"] == 2, caution
    assert controlled["count"] == 2, controlled
    assert caution["mean_p_top"] == (0.25 + 0.375) / 2, caution
    assert controlled["mean_p_top"] == (0.25 + 0.5) / 2, controlled
    assert caution["min_p_top"] == 0.25 and caution["max_p_top"] == 0.375, caution
    assert rep["near_boundary_count"] >= 1, rep
    assert any(e["server_id"] == "srv_b2" and abs(e["headroom"] - 0.05) < 1e-9
               for e in controlled["near_boundary"]), controlled
    assert rep["boundary"] == TIER_BOUNDARIES, rep

    # --- edge cases: unusable p_top, unknown tier, empty input ---
    assert tier_headroom([])["tiers"] == {}
    assert tier_headroom([{"server_id": "x", "risk_tier": "CAUTION_LIMITED",
                           "p_top": None}])["tiers"] == {}
    assert tier_headroom([{"server_id": "x", "risk_tier": "", "p_top": 0.2}])["tiers"] == {}
    unknown = tier_headroom([{"server_id": "x", "risk_tier": "WEIRD_TIER", "p_top": 0.2}])
    assert unknown["tiers"]["WEIRD_TIER"]["count"] == 1, unknown
    assert unknown["tiers"]["WEIRD_TIER"]["near_boundary"] == [], unknown

    # --- real data layer path (SQLite over the REAL app models) ---
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    for sid, name, tier, p in (("srv_a1", "Alpha MCP", "CAUTION_LIMITED", 0.25),
                               ("srv_a2", "Beta MCP", "CAUTION_LIMITED", 0.375),
                               ("srv_b1", "Gamma MCP", "ENTERPRISE_CONTROLLED", 0.25),
                               ("srv_b2", "Delta MCP", "ENTERPRISE_CONTROLLED", 0.5)):
        s.add(McpServerRegistry(server_id=sid, name=name, risk_tier=tier,
                                url=f"https://github.com/example/{sid}"))
    for i, (sid, p) in enumerate((("srv_a1", 0.25), ("srv_a2", 0.375),
                                  ("srv_b1", 0.25), ("srv_b2", 0.5)), start=1):
        s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name="overall_risk",
                              label="HIGH", p_top=p, model_version="v3.0_40974559"))
    s.commit()

    live = build_report(s)
    assert "tiers" in live, live
    assert set(live["tiers"]) == {"CAUTION_LIMITED", "ENTERPRISE_CONTROLLED"}, live
    assert live["tiers"]["CAUTION_LIMITED"]["count"] == 2, live
    assert live["tiers"]["CAUTION_LIMITED"]["mean_p_top"] == (0.25 + 0.375) / 2, live
    assert live["near_boundary_count"] >= 1, live
    assert "generated_at" in live, live

    # callable form of the session argument
    callable_rep = build_report(lambda: s)
    assert callable_rep["tiers"] == live["tiers"], callable_rep

    # --- endpoint over the REAL data layer (SQLite override of get_session) ---
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

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
    r = c.get("/api/reports/risk-tier-headroom")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["tiers"]) == {"CAUTION_LIMITED", "ENTERPRISE_CONTROLLED"}, body
    assert body["tiers"]["ENTERPRISE_CONTROLLED"]["count"] == 2, body
    s.close()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
