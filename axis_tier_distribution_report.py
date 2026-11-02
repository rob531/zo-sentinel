# deps: sqlalchemy
"""axis_tier_distribution_report.py -- per-axis risk-band distribution report.

Reads the SFT per-axis scores (mcp_llm_axis_scores, current model_version) and
reports, for every axis (the 6 non-overall axes plus overall_risk), how many
servers fall into each risk band derived from the model's `p_top`.

Two layers:
  * pure report functions -- generate_axis_tier_distribution(rows) /
    summarize_axis_tiers(rows) take the rows from the caller and touch nothing
    else (no network, no file writes, no DB).
  * the data seam -- load_axis_rows(db, model_version) reads the REAL app
    table through the REAL app session dependency (app.db.get_session) and
    app.models.McpLlmAxisScore, returning rows in the shape the report
    functions expect.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

DEFAULT_BAND_EDGES: Tuple[float, ...] = (0.33, 0.66)

_BAND_NAMES_3 = ("LOW", "MEDIUM", "HIGH")


# ---------------------------------------------------------------- pure layer

def _band_names(n_bands: int) -> List[str]:
    """Band labels in band_edges order (lowest band first)."""
    if n_bands == 3:
        return list(_BAND_NAMES_3)
    return [f"BAND_{i + 1}" for i in range(n_bands)]


def _validate_edges(band_edges: Tuple[float, ...]) -> Tuple[float, ...]:
    edges = tuple(float(e) for e in (band_edges or ()))
    for e in edges:
        if e < 0.0 or e > 1.0:
            raise ValueError(f"band edges must be within [0, 1]: {band_edges!r}")
    for a, b in zip(edges, edges[1:]):
        if b <= a:
            raise ValueError(f"band edges must be strictly ascending: {band_edges!r}")
    return edges


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


def _band_index(p: float, edges: Tuple[float, ...]) -> int:
    """Index of the band p falls into: band i spans [edges[i-1], edges[i])."""
    idx = 0
    for e in edges:
        if p >= e:
            idx += 1
    return idx


def generate_axis_tier_distribution(
    rows: List[Dict[str, Any]],
    band_edges: Tuple[float, ...] = DEFAULT_BAND_EDGES,
    axis_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Tier distribution for ONE axis.

    rows: dicts with keys axis_name (str), server_id (str), p_top (float).
    Rows whose axis differs from the reported axis are ignored; rows without a
    usable p_top are ignored. Duplicate rows for the same server on the same
    axis collapse to the highest p_top (the most conservative reading).

    Returns {"axis_name": str, "bands": [{"band": str, "count": int,
    "share": float}], "total": int}. Bands are emitted in band_edges order
    (lowest band first); shares are fractions of the axis total.
    """
    edges = _validate_edges(band_edges)
    names = _band_names(len(edges) + 1)

    cleaned: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        sid = str(row.get("server_id") or "").strip()
        p = _coerce_p_top(row)
        if not axis or not sid or p is None:
            continue
        cleaned.append({"axis_name": axis, "server_id": sid, "p_top": p})

    if axis_name is None:
        axis_name = ""
        if cleaned:
            counts: Dict[str, int] = {}
            for r in cleaned:
                counts[r["axis_name"]] = counts.get(r["axis_name"], 0) + 1
            axis_name = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]

    best: Dict[str, float] = {}
    for r in cleaned:
        if r["axis_name"] != axis_name:
            continue
        sid = r["server_id"]
        if sid not in best or r["p_top"] > best[sid]:
            best[sid] = r["p_top"]

    total = len(best)
    band_counts = [0] * (len(edges) + 1)
    for p in best.values():
        band_counts[_band_index(p, edges)] += 1

    bands = [
        {
            "band": names[i],
            "count": band_counts[i],
            "share": round(band_counts[i] / total, 6) if total else 0.0,
        }
        for i in range(len(band_counts))
    ]

    return {"axis_name": axis_name, "bands": bands, "total": total}


def summarize_axis_tiers(
    rows: List[Dict[str, Any]],
    band_edges: Tuple[float, ...] = DEFAULT_BAND_EDGES,
) -> List[Dict[str, Any]]:
    """One tier-distribution entry per axis, axes sorted by name."""
    by_axis: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if not axis:
            continue
        by_axis.setdefault(axis, []).append(row)

    return [
        generate_axis_tier_distribution(by_axis[axis], band_edges=band_edges,
                                        axis_name=axis)
        for axis in sorted(by_axis)
    ]


def format_text_report(summary: List[Dict[str, Any]]) -> str:
    """Render the per-axis tier distribution as a plain-text report."""
    lines: List[str] = []
    lines.append("Axis tier distribution (p_top bands)")
    lines.append("=" * 38)
    for entry in summary or []:
        lines.append(f"{entry.get('axis_name', '')}  (total {entry.get('total', 0)})")
        for band in entry.get("bands") or []:
            share = band.get("share", 0.0)
            lines.append(f"  {band.get('band', ''):<8} "
                         f"{band.get('count', 0):>6}  {share * 100:6.2f}%")
        lines.append("")
    if not summary:
        lines.append("(no scored rows)")
    return "\n".join(lines)


# --------------------------------------------------------------- data seam

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
    ).where(McpLlmAxisScore.p_top.isnot(None))
    if mv:
        stmt = stmt.where(McpLlmAxisScore.model_version == mv)
    return [
        {"server_id": sid, "axis_name": axis, "p_top": p_top}
        for sid, axis, p_top in db.execute(stmt).all()
    ]


# ------------------------------------------------------------------- CLI

def _self_test() -> int:
    # --- pure layer: 2 axes, known p_top values ---
    rows = [
        {"axis_name": "overall_risk", "server_id": "s1", "p_top": 0.10},
        {"axis_name": "overall_risk", "server_id": "s2", "p_top": 0.50},
        {"axis_name": "overall_risk", "server_id": "s3", "p_top": 0.90},
        {"axis_name": "auth_strength", "server_id": "s1", "p_top": 0.20},
        {"axis_name": "auth_strength", "server_id": "s2", "p_top": 0.70},
    ]

    summary = summarize_axis_tiers(rows)
    assert [e["axis_name"] for e in summary] == ["auth_strength", "overall_risk"], summary

    overall = next(e for e in summary if e["axis_name"] == "overall_risk")
    assert overall["total"] == 3, overall
    assert [b["band"] for b in overall["bands"]] == ["LOW", "MEDIUM", "HIGH"], overall
    assert [b["count"] for b in overall["bands"]] == [1, 1, 1], overall
    assert sum(b["count"] for b in overall["bands"]) == overall["total"], overall
    assert abs(sum(b["share"] for b in overall["bands"]) - 1.0) < 1e-4, overall

    auth = next(e for e in summary if e["axis_name"] == "auth_strength")
    assert auth["total"] == 2, auth
    assert [b["count"] for b in auth["bands"]] == [1, 0, 1], auth
    assert sum(b["count"] for b in auth["bands"]) == auth["total"], auth

    # band_edges order drives the band order
    custom = generate_axis_tier_distribution(
        [r for r in rows if r["axis_name"] == "overall_risk"],
        band_edges=(0.2, 0.5, 0.8))
    assert [b["band"] for b in custom["bands"]] == ["BAND_1", "BAND_2", "BAND_3", "BAND_4"], custom
    assert custom["total"] == 3, custom
    assert sum(b["count"] for b in custom["bands"]) == custom["total"], custom

    # edge cases: empty input, missing p_top, duplicate server rows
    empty = generate_axis_tier_distribution([])
    assert empty["total"] == 0, empty
    assert all(b["count"] == 0 and b["share"] == 0.0 for b in empty["bands"]), empty

    no_p = generate_axis_tier_distribution([{"axis_name": "overall_risk",
                                             "server_id": "s9", "p_top": None}])
    assert no_p["total"] == 0, no_p

    dupes = generate_axis_tier_distribution([
        {"axis_name": "overall_risk", "server_id": "s1", "p_top": 0.10},
        {"axis_name": "overall_risk", "server_id": "s1", "p_top": 0.95},
    ])
    assert dupes["total"] == 1, dupes
    assert dupes["bands"][2]["count"] == 1, dupes

    # --- DB path: real app models over SQLite ---
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
        for i, (sid, ax, p) in enumerate((
                ("srv1", "overall_risk", 0.91),
                ("srv1", "auth_strength", 0.12),
                ("srv2", "overall_risk", 0.44)), start=1):
            s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name=ax, label="HIGH",
                                  p_top=p, model_version="v3.0_40974559"))
        s.commit()
        loaded = load_axis_rows(s)
        assert len(loaded) == 3, loaded
        rep = summarize_axis_tiers(loaded)
        assert [e["axis_name"] for e in rep] == ["auth_strength", "overall_risk"], rep
        overall = next(e for e in rep if e["axis_name"] == "overall_risk")
        assert overall["total"] == 2, overall
        assert [b["count"] for b in overall["bands"]] == [0, 1, 1], overall
        assert "overall_risk" in format_text_report(rep)
    finally:
        s.close()

    print("PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Per-axis tier distribution report")
    ap.add_argument("--db", action="store_true",
                    help="read the real mcp_llm_axis_scores rows via app.db.get_session")
    ap.add_argument("--model-version", default=None, help="restrict to one model_version")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--self-test", action="store_true", help="run the built-in self-test")
    args = ap.parse_args(argv)

    if args.db:
        gen = get_session()
        db = next(gen)
        try:
            rows = load_axis_rows(db, model_version=args.model_version)
        finally:
            gen.close()
        summary = summarize_axis_tiers(rows)
        if args.format == "json":
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print(format_text_report(summary))
        return 0

    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())
