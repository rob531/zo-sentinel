"""build_score_spread_distribution_report.py -- fleet distribution report for LLM axis scores.

Reads the per-axis p_top scores (mcp_llm_axis_scores, ~65k rows) and summarises
how those scores are distributed across the fleet: per-axis min/max/mean/median/p90,
a 10-bin histogram, and the escalated ratio.

Two layers:
  * build_report(rows) -- PURE computation over plain dicts (stdlib only).
  * load_rows(db) / router endpoint -- the REAL app data layer
    (app.db.get_session + app.models.McpLlmAxisScore), so the report is fed from
    the authoritative Postgres table rather than a local copy.

CLI: python build_score_spread_distribution_report.py --out report.md
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["reports"])

HIST_BINS = 10          # 10 buckets of width 0.1 over [0.0, 1.0]
REPORT_NAME = "score_spread_distribution"


# ----------------------------- pure computation -----------------------------

def _percentile(sorted_vals: List[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile (the 'inclusive'/numpy default).

    index = (n - 1) * q ; value = a[lo] + frac * (a[hi] - a[lo]).
    """
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _bin_index(value: float) -> int:
    """Bucket index for a p_top in [0, 1]; 1.0 lands in the last bin."""
    d = Decimal(str(value)) * HIST_BINS
    idx = int(d.to_integral_value(rounding=ROUND_FLOOR))
    if idx < 0:
        return 0
    if idx >= HIST_BINS:
        return HIST_BINS - 1
    return idx


def _histogram(values: Iterable[float]) -> List[Dict[str, Any]]:
    counts = [0] * HIST_BINS
    for v in values:
        counts[_bin_index(v)] += 1
    bins = []
    for i, c in enumerate(counts):
        lo = round(i / HIST_BINS, 4)
        hi = round((i + 1) / HIST_BINS, 4)
        bins.append({"lo": lo, "hi": hi, "count": c})
    return bins


def build_report(rows: List[dict]) -> dict:
    """Summarise the p_top distribution of the given axis-score rows.

    rows: dicts with keys axis_name, p_top, escalated (optional), server_id.
    """
    grouped: Dict[str, List[dict]] = {}
    for r in rows or []:
        axis = (r.get("axis_name") or "unknown").strip() or "unknown"
        grouped.setdefault(axis, []).append(r)

    axes_out: List[dict] = []
    for axis in sorted(grouped):
        group = grouped[axis]
        vals = sorted(float(r["p_top"]) for r in group
                      if r.get("p_top") is not None)
        count = len(vals)
        escalated = sum(1 for r in group if r.get("escalated"))
        axes_out.append({
            "axis_name": axis,
            "count": count,
            "min_p_top": round(vals[0], 6) if vals else None,
            "max_p_top": round(vals[-1], 6) if vals else None,
            "mean_p_top": round(statistics.fmean(vals), 6) if vals else None,
            "median_p_top": round(statistics.median(vals), 6) if vals else None,
            "p90_p_top": (round(_percentile(vals, 0.90), 6)
                          if vals else None),
            "hist_bins": _histogram(vals),
            "escalated_count": escalated,
            "escalated_ratio": round(escalated / count, 6) if count else 0.0,
        })

    servers = {r.get("server_id") for r in (rows or []) if r.get("server_id")}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": REPORT_NAME,
        "axes": axes_out,
        "summary": {
            "total_rows": len(rows or []),
            "axis_count": len(axes_out),
            "servers_seen": len(servers),
        },
    }


# ------------------------------- rendering ----------------------------------

def render_markdown(report: dict) -> str:
    """Render the report dict as human-readable markdown."""
    out: List[str] = []
    out.append(f"# {REPORT_NAME.replace('_', ' ').title()}")
    out.append("")
    out.append(f"_Generated at {report.get('generated_at')}_")
    s = report.get("summary", {})
    out.append("")
    out.append(f"- total_rows: {s.get('total_rows', 0)}")
    out.append(f"- axis_count: {s.get('axis_count', 0)}")
    out.append(f"- servers_seen: {s.get('servers_seen', 0)}")
    out.append("")
    out.append("| axis | n | min | mean | median | p90 | max | escalated | ratio |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for a in report.get("axes", []):
        out.append("| {axis} | {n} | {mn} | {mean} | {med} | {p90} | {mx} | "
                   "{esc} | {ratio} |".format(
                           axis=a.get("axis_name"), n=a.get("count"),
                           mn=a.get("min_p_top"), mean=a.get("mean_p_top"),
                           med=a.get("median_p_top"), p90=a.get("p90_p_top"),
                           mx=a.get("max_p_top"), esc=a.get("escalated_count"),
                           ratio=a.get("escalated_ratio")))
    for a in report.get("axes", []):
        out.append("")
        out.append(f"### {a.get('axis_name')}")
        out.append("")
        out.append("| bin | count |")
        out.append("|---|---|")
        for b in a.get("hist_bins", []):
            out.append(f"| [{b.get('lo')}, {b.get('hi')}] | {b.get('count')} |")
    out.append("")
    return "\n".join(out)


# ------------------------------ data layer ----------------------------------

def load_rows(db: Session, model_version: Optional[str] = None) -> List[dict]:
    """Read the axis-score rows from the authoritative table."""
    stmt = select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
                  McpLlmAxisScore.p_top, McpLlmAxisScore.escalated)
    if model_version:
        stmt = stmt.where(McpLlmAxisScore.model_version == model_version)
    return [{"server_id": sid, "axis_name": axis, "p_top": p_top,
             "escalated": escalated}
            for sid, axis, p_top, escalated in db.execute(stmt).all()]


@router.get("/reports/score-spread-distribution")
def score_spread_distribution(model_version: Optional[str] = None,
                               db: Session = Depends(get_session)) -> dict:
    """Distribution report over the fleet's LLM axis scores."""
    return build_report(load_rows(db, model_version))


# ---------------------------------- CLI -------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=REPORT_NAME)
    ap.add_argument("--out", help="write the rendered markdown here")
    ap.add_argument("--model-version", default=None)
    ap.add_argument("--json", action="store_true", help="print the raw report dict")
    args = ap.parse_args(argv)

    from app.db import SessionLocal
    db = SessionLocal()
    try:
        report = build_report(load_rows(db, args.model_version))
    finally:
        db.close()

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    md = render_markdown(report)
    if args.out:
        path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"wrote {path}")
    else:
        print(md)
    return 0


# ------------------------------- self-test ----------------------------------

def _seed_corpus() -> List[dict]:
    """Synthetic corpus: 200 rows, p_top in 0.0..1.0 by 0.05, one axis of 20 rows."""
    axes = ["overall_risk", "auth_strength", "capability_breadth",
            "data_sensitivity", "network_egress", "maintainer_trust",
            "exploit_surface"]
    rows: List[dict] = []
    # forced axis: exactly 20 rows, p_top = 0.00 .. 0.95
    for i in range(20):
        p = round(0.05 * i, 2)
        rows.append({"server_id": f"srv-forced-{i}", "axis_name": axes[0],
                     "p_top": p, "escalated": p >= 0.70})
    # remaining axes: 30 rows each -> 6 * 30 = 180 ; 20 + 180 = 200
    for a_i, axis in enumerate(axes[1:]):
        for i in range(30):
            p = round(0.05 * (i % 21), 2)
            rows.append({"server_id": f"srv-{a_i}-{i}", "axis_name": axis,
                         "p_top": p, "escalated": p >= 0.9})
    return rows


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # ---- pure-computation acceptance ----
    rows = _seed_corpus()
    assert len(rows) == 200, len(rows)
    rep = build_report(rows)
    axes = {a["axis_name"]: a for a in rep["axes"]}
    assert len(axes) == 7, sorted(axes)

    forced = axes["overall_risk"]
    assert forced["count"] == 20, forced
    # sorted values 0.00..0.95 -> median = mean of the 10th and 11th = 0.475
    assert abs(forced["median_p_top"] - 0.475) <= 0.01, forced["median_p_top"]
    # p90: index (20-1)*0.9 = 17.1 -> 0.85 + 0.1*(0.90-0.85) = 0.855
    assert abs(forced["p90_p_top"] - 0.855) <= 0.01, forced["p90_p_top"]
    assert abs(forced["mean_p_top"] - (sum(round(0.05 * i, 2) for i in range(20)) / 20)) <= 0.01
    assert forced["min_p_top"] == 0.0 and forced["max_p_top"] == 0.95, forced
    # histogram bins sum to the row count
    for a in rep["axes"]:
        assert sum(b["count"] for b in a["hist_bins"]) == a["count"], a["axis_name"]
        assert len(a["hist_bins"]) == HIST_BINS, a["axis_name"]
    # escalated ratio: 6 of 20 forced rows (p_top >= 0.70)
    assert forced["escalated_count"] == 6, forced
    assert abs(forced["escalated_ratio"] - 0.3) <= 0.001, forced
    # fleet summary
    assert rep["summary"]["total_rows"] == 200, rep["summary"]
    assert rep["summary"]["axis_count"] == 7, rep["summary"]
    assert rep["summary"]["servers_seen"] == 200, rep["summary"]
    assert rep["generated_at"], rep

    md = render_markdown(rep)
    assert "overall_risk" in md and "p90" in md, md[:200]

    # empty input must not explode
    empty = build_report([])
    assert empty["summary"]["total_rows"] == 0 and empty["axes"] == [], empty

    # ---- the real data layer path (SQLite via dependency override) ----
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    for i, ax in enumerate(("overall_risk", "auth_strength"), start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax,
                              label="HIGH", model_version="v3.0_40974559"))
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
    client = TestClient(app)
    r = client.get("/api/reports/score-spread-distribution")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["summary"]["total_rows"] == 2, j
    assert {a["axis_name"] for a in j["axes"]} == {"overall_risk", "auth_strength"}, j


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] not in ("--self-test",):
        raise SystemExit(main(sys.argv[1:]))
    _run_self_test()
    print("PASS")
