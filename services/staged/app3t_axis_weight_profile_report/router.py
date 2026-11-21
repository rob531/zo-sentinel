# deps: fastapi, sqlalchemy
"""app3t_axis_weight_profile_report.py -- how each of the 7 LLM risk axes relates to
the overall_risk score across the scored fleet.

Two layers:
  * pure layer -- build_axis_weight_profile(rows) / render_axis_weight_profile(profile)
    take the axis-score rows from the caller and touch nothing else (stdlib only,
    no network, no DB writes).
  * data seam -- load_axis_rows(db) reads the REAL app table through the REAL app
    session dependency (app.db.get_session) and app.models.McpLlmAxisScore.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

router = APIRouter(prefix="/api", tags=["axis-weight-profile"])


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _pearson(pairs: List[Tuple[float, float]]) -> float:
    """Pearson r over (x, y) pairs. 0.0 when there is no variance to explain."""
    n = len(pairs)
    if n < 2:
        return 0.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx = sum(xs) / n
    my = sum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    num = sum(a * b for a, b in zip(dx, dy))
    vx = math.sqrt(sum(a * a for a in dx))
    vy = math.sqrt(sum(a * a for a in dy))
    if vx < 1e-12 and vy < 1e-12:
        return 1.0   # both series constant and identical -> perfectly correlated
    if vx < 1e-12 or vy < 1e-12:
        return 0.0
    return num / (vx * vy)


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


# ---------------------------------------------------------------- pure layer

def build_axis_weight_profile(rows: List[Dict[str, Any]], min_rows: int = 50) -> Dict[str, Any]:
    """Profile each risk axis against overall_risk across the scored fleet.

    rows: dicts with keys server_id (str), axis_name (str, one of AXES), p_top
    (float), scored_at (iso string or datetime, informational only).

    Returns {axis_name: {"n": int, "mean_p_top": float,
                         "pearson_r_with_overall": float, "spread": float}}
    for all 7 axes, where spread is the population standard deviation of p_top.
    When len(rows) < min_rows the sample is too small to correlate honestly, so
    {'insufficient_data': True, 'n': len(rows)} is returned instead of invented
    correlations.
    """
    rows = list(rows or [])
    if len(rows) < min_rows:
        return {"insufficient_data": True, "n": len(rows)}

    per_server: Dict[str, Dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if axis not in AXES:
            continue
        server_id = str(row.get("server_id") or "").strip()
        p_top = _finite(row.get("p_top"))
        if not server_id or p_top is None:
            continue
        per_server.setdefault(server_id, {})[axis] = p_top

    overall_by_server: Dict[str, float] = {
        sid: axes["overall_risk"]
        for sid, axes in per_server.items()
        if "overall_risk" in axes
    }

    profile: Dict[str, Any] = {}
    for axis in AXES:
        values: List[float] = []
        pairs: List[Tuple[float, float]] = []
        for sid, axes in per_server.items():
            if axis not in axes:
                continue
            values.append(axes[axis])
            if axis == "overall_risk":
                pairs.append((axes[axis], axes[axis]))
            elif sid in overall_by_server:
                pairs.append((axes[axis], overall_by_server[sid]))
        n = len(values)
        mean = (sum(values) / n) if n else 0.0
        profile[axis] = {
            "n": n,
            "mean_p_top": round(mean, 6),
            "pearson_r_with_overall": round(_pearson(pairs), 6),
            "spread": round(_stdev(values), 6),
        }
    return profile


def render_axis_weight_profile(profile: Dict[str, Any]) -> str:
    """Plain-text rendering of an axis weight profile."""
    profile = profile or {}
    if profile.get("insufficient_data"):
        n = profile.get("n", 0)
        return ("AXIS WEIGHT PROFILE\n"
                "insufficient data: %d rows (minimum sample not reached) -- "
                "no correlations computed\n" % n)

    lines = ["AXIS WEIGHT PROFILE (each axis vs overall_risk across the fleet)", ""]
    lines.append("%-20s %7s %14s %12s %10s" % ("axis", "n", "mean_p_top",
                                               "pearson_r", "spread"))
    lines.append("-" * 68)
    for axis in AXES:
        entry = profile.get(axis) or {}
        lines.append("%-20s %7d %14.6f %12.6f %10.6f" % (
            axis,
            int(entry.get("n", 0) or 0),
            float(entry.get("mean_p_top", 0.0) or 0.0),
            float(entry.get("pearson_r_with_overall", 0.0) or 0.0),
            float(entry.get("spread", 0.0) or 0.0),
        ))
    lines.append("")
    lines.append("pearson_r_with_overall: correlation of the axis p_top with the "
                 "server's overall_risk p_top")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- data seam

def _latest_model_version(db: Session) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.p_top.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def load_axis_rows(db: Session, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the REAL axis scores and return them as report rows."""
    mv = model_version or _latest_model_version(db)
    stmt = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.scored_at,
    ).where(McpLlmAxisScore.p_top.isnot(None))
    if mv:
        stmt = stmt.where(McpLlmAxisScore.model_version == mv)
    return [
        {
            "server_id": sid,
            "axis_name": axis,
            "p_top": p_top,
            "scored_at": scored_at.isoformat() if scored_at else None,
        }
        for sid, axis, p_top, scored_at in db.execute(stmt).all()
    ]


@router.get("/axis-weight-profile")
def axis_weight_profile(min_rows: int = 50,
                        db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Read-only axis weight profile over the scored fleet."""
    rows = load_axis_rows(db)
    profile = build_axis_weight_profile(rows, min_rows=min_rows)
    return {"profile": profile, "report": render_axis_weight_profile(profile)}


# ---------------------------------------------------------------------- CLI

def run(model_version: Optional[str] = None, min_rows: int = 50) -> str:
    """Read the real fleet through app.db.get_session and print the report."""
    gen = get_session()
    db = next(gen)
    try:
        rows = load_axis_rows(db, model_version=model_version)
    finally:
        gen.close()
    profile = build_axis_weight_profile(rows, min_rows=min_rows)
    report = render_axis_weight_profile(profile)
    print(report)
    return report


def _self_test() -> int:
    # --- pure layer: perfectly correlated axis ---
    n_servers = 60
    rng = random.Random(20261121)
    rows: List[Dict[str, Any]] = []
    for i in range(n_servers):
        sid = "srv%03d" % i
        ramp = i / (n_servers - 1)
        noise = rng.random()
        for axis in AXES:
            if axis == "overall_risk":
                p_top = ramp
            elif axis == "auth_strength":
                p_top = ramp                      # perfectly correlated
            elif axis == "capability_breadth":
                p_top = noise                    # uncorrelated
            else:
                p_top = ramp * 0.5 + noise * 0.5
            rows.append({"server_id": sid, "axis_name": axis, "p_top": p_top,
                         "scored_at": "2026-11-21T00:00:00+00:00"})

    profile = build_axis_weight_profile(rows)
    assert "insufficient_data" not in profile, profile
    assert set(profile) == set(AXES), sorted(profile)
    auth = profile["auth_strength"]
    assert auth["n"] == n_servers, auth
    assert abs(auth["pearson_r_with_overall"] - 1.0) < 1e-6, auth
    assert abs(profile["overall_risk"]["pearson_r_with_overall"] - 1.0) < 1e-6, profile
    breadth = profile["capability_breadth"]
    assert abs(breadth["pearson_r_with_overall"]) < 0.3, breadth
    assert breadth["mean_p_top"] > 0.0 and breadth["spread"] > 0.0, breadth

    # --- pure layer: too-small sample ---
    small = build_axis_weight_profile(rows[:5], min_rows=50)
    assert small == {"insufficient_data": True, "n": 5}, small

    # --- rendering ---
    report = render_axis_weight_profile(profile)
    for axis in AXES:
        assert axis in report, axis
    assert "insufficient data" in render_axis_weight_profile(small)

    # --- data seam: real app models over SQLite (dependency override) ---
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
    try:
        sid_to_id = 1
        for i in range(n_servers):
            sid = "srv%03d" % i
            ramp = i / (n_servers - 1)
            for axis in AXES:
                s.add(McpLlmAxisScore(id=sid_to_id, server_id=sid, axis_name=axis,
                                      label="HIGH", model_version="v3.0_40974559",
                                      p_top=ramp))
                sid_to_id += 1
        s.commit()
    finally:
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
    client = TestClient(app)
    resp = client.get("/api/axis-weight-profile")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert set(payload["profile"]) == set(AXES), payload
    assert payload["profile"]["auth_strength"]["pearson_r_with_overall"] > 0.99, payload
    assert "overall_risk" in payload["report"], payload

    resp_small = client.get("/api/axis-weight-profile?min_rows=100000")
    assert resp_small.status_code == 200, resp_small.text
    assert resp_small.json()["profile"].get("insufficient_data") is True, resp_small.json()

    print("PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Axis weight profile report")
    ap.add_argument("--model-version", default=None, help="restrict to one model_version")
    ap.add_argument("--min-rows", type=int, default=50, help="minimum rows for correlations")
    ap.add_argument("--json", action="store_true", help="print the profile as JSON")
    ap.add_argument("--self-test", action="store_true", help="run the built-in self-test")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    gen = get_session()
    db = next(gen)
    try:
        rows = load_axis_rows(db, model_version=args.model_version)
    finally:
        gen.close()
    profile = build_axis_weight_profile(rows, min_rows=args.min_rows)
    if args.json:
        print(json.dumps(profile, indent=2, sort_keys=True))
    else:
        print(render_axis_weight_profile(profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
