# deps: sqlalchemy
"""axis_probability_calibration_report.py -- reliability/calibration table for the
LLM axis model.

Measures how well the model's `p_top` probabilities line up with the tier
labels actually observed per server (mcp_llm_axis_scores). For every one of the
7 real axes it builds a 10-bucket reliability table over [0, 1]: bucket share of
high-risk labels vs the bucket's mean predicted probability, plus the
mean absolute calibration error.

Two layers:
  * pure report functions -- build_report(rows) / format_markdown(report) --
    take the rows from the caller and touch nothing else (no network, no
    file writes, no DB).
  * the data seam -- load_axis_rows(db, model_version) reads the REAL app
    table through the REAL app session dependency (app.db.get_session) and
    app.models.McpLlmAxisScore, returning rows in the shape build_report
    expects.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

HIGH_RISK_KEYWORDS = ("high", "critical", "danger")
N_BINS = 10


# ---------------------------------------------------------------- pure layer

def _is_high_risk(label: Any) -> bool:
    low = str(label or "").strip().lower()
    return any(k in low for k in HIGH_RISK_KEYWORDS)


def _bin_index(p_top: float) -> int:
    p = min(1.0, max(0.0, float(p_top)))
    return min(int(p * N_BINS), N_BINS - 1)


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


def _empty_bin(i: int) -> Dict[str, Any]:
    return {"lo": round(i / N_BINS, 4), "hi": round((i + 1) / N_BINS, 4),
            "n": 0, "mean_p_top": 0.0, "observed_rate": 0.0}


def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the per-axis calibration report from caller-supplied rows.

    Rows whose axis_name is not one of the 7 real axes are ignored (never an
    error). Rows without a usable p_top are ignored too.
    """
    per_axis: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if axis not in AXES:
            continue
        p = _coerce_p_top(row)
        if p is None:
            continue
        per_axis.setdefault(axis, []).append({
            "p_top": p,
            "label": row.get("label"),
            "escalated": bool(row.get("escalated")),
            "server_id": row.get("server_id"),
        })

    axes_out: Dict[str, Any] = {}
    total_n = 0
    weighted_error = 0.0

    for axis in AXES:
        items = per_axis.get(axis)
        if not items:
            continue
        n = len(items)
        total_n += n
        bins = [_empty_bin(i) for i in range(N_BINS)]
        sums = [0.0] * N_BINS
        highs = [0] * N_BINS
        counts = [0] * N_BINS
        for it in items:
            idx = _bin_index(it["p_top"])
            counts[idx] += 1
            sums[idx] += it["p_top"]
            if _is_high_risk(it["label"]):
                highs[idx] += 1
        axis_error = 0.0
        for i in range(N_BINS):
            if counts[i] == 0:
                continue
            mean_p = sums[i] / counts[i]
            observed = highs[i] / counts[i]
            bins[i]["n"] = counts[i]
            bins[i]["mean_p_top"] = round(mean_p, 6)
            bins[i]["observed_rate"] = round(observed, 6)
            axis_error += (counts[i] / n) * abs(mean_p - observed)
        mean_p_top = sum(it["p_top"] for it in items) / n
        axes_out[axis] = {
            "n": n,
            "mean_p_top": round(mean_p_top, 6),
            "mean_abs_calibration_error": round(axis_error, 6),
            "bins": bins,
        }
        weighted_error += (n / total_n) * axis_error if total_n else 0.0

    return {
        "axes": axes_out,
        "overall": {"n": total_n,
                    "mean_abs_calibration_error": round(weighted_error, 6)},
    }


def format_markdown(report: Dict[str, Any]) -> str:
    """Render the per-axis reliability table as Markdown."""
    report = report or {}
    axes = report.get("axes") or {}
    overall = report.get("overall") or {}
    lines: List[str] = []
    lines.append("## Axis probability calibration")
    lines.append("")
    lines.append(f"rows: {overall.get('n', 0)} | "
                 f"mean abs calibration error: {overall.get('mean_abs_calibration_error', 0.0)}")
    lines.append("")
    lines.append("| axis | bin | n | mean_p_top | observed_rate | gap |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for axis in AXES:
        info = axes.get(axis)
        if not info:
            continue
        for b in info.get("bins") or []:
            gap = round(abs(b.get("mean_p_top", 0.0) - b.get("observed_rate", 0.0)), 6)
            lines.append(
                f"| {axis} | {b.get('lo', 0.0):.1f}-{b.get('hi', 0.0):.1f} | "
                f"{b.get('n', 0)} | {b.get('mean_p_top', 0.0):.4f} | "
                f"{b.get('observed_rate', 0.0):.4f} | {gap:.4f} |")
    return "\n".join(lines)


# --------------------------------------------------------------- data seam

def _latest_model_version(db: Session) -> Optional[str]:
    rows = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.p_top.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).scalars().all()
    return rows[0] if rows else None


def load_axis_rows(db: Session, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the REAL axis scores and return them as build_report() rows."""
    mv = model_version or _latest_model_version(db)
    stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.p_top.isnot(None))
    if mv:
        stmt = stmt.where(McpLlmAxisScore.model_version == mv)
    out: List[Dict[str, Any]] = []
    for r in db.execute(stmt).scalars().all():
        out.append({
            "server_id": r.server_id,
            "axis_name": r.axis_name,
            "p_top": r.p_top,
            "label": r.label,
            "escalated": bool(r.escalated),
        })
    return out


# ------------------------------------------------------------------- CLI

def _self_test() -> int:
    import random
    random.seed(7)

    axes_used = ["overall_risk", "auth_strength", "data_sensitivity"]
    rows: List[Dict[str, Any]] = []
    for i in range(36):
        axis = axes_used[i % len(axes_used)]
        p = round(random.random(), 4)
        label = "HIGH" if p > 0.7 else ("CRITICAL" if p > 0.9 else "LOW")
        rows.append({"server_id": f"srv-{i % 5}", "axis_name": axis,
                     "p_top": p, "label": label, "escalated": False})
    for i in range(3):
        rows.append({"server_id": f"srv-{i}", "axis_name": "not_a_real_axis",
                     "p_top": 0.5, "label": "HIGH", "escalated": False})
    assert len(rows) >= 30
    valid = [r for r in rows if r["axis_name"] in AXES]
    assert len(valid) == len(rows) - 3

    report = build_report(rows)

    # (b) invalid axis ignored
    assert "not_a_real_axis" not in report["axes"], report["axes"].keys()
    assert set(report["axes"]) == set(axes_used), report["axes"].keys()

    # (c) bucket counts sum to n, and n == valid supplied rows
    for axis in axes_used:
        info = report["axes"][axis]
        expected = sum(1 for r in valid if r["axis_name"] == axis)
        assert info["n"] == expected, (axis, info["n"], expected)
        assert sum(b["n"] for b in info["bins"]) == info["n"], axis
    assert report["overall"]["n"] == len(valid)

    # (d) all probabilities / observed rates inside [0, 1]
    for axis, info in report["axes"].items():
        assert 0.0 <= info["mean_p_top"] <= 1.0, axis
        for b in info["bins"]:
            assert 0.0 <= b["mean_p_top"] <= 1.0, b
            assert 0.0 <= b["observed_rate"] <= 1.0, b
    assert 0.0 <= report["overall"]["mean_abs_calibration_error"] <= 1.0

    # (e) markdown rendering
    md = format_markdown(report)
    assert "|" in md, md
    for axis in axes_used:
        assert axis in md, axis

    # real data layer wiring: read the real table through the real model
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    try:
        for i, (ax, lbl, p) in enumerate((
                ("overall_risk", "HIGH", 0.91),
                ("auth_strength", "STRONG", 0.12),
                ("data_sensitivity", "CRITICAL", 0.77)), start=1):
            s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax, label=lbl,
                                  p_top=p, model_version="v3.0_40974559"))
        s.commit()
        loaded = load_axis_rows(s)
        assert len(loaded) == 3, loaded
        assert {r["axis_name"] for r in loaded} == {"overall_risk", "auth_strength",
                                                    "data_sensitivity"}
        rep2 = build_report(loaded)
        assert rep2["overall"]["n"] == 3, rep2
        assert "overall_risk" in format_markdown(rep2)
    finally:
        s.close()

    print("PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="LLM axis probability calibration report")
    ap.add_argument("--db", action="store_true",
                    help="read the real mcp_llm_axis_scores rows via app.db.get_session")
    ap.add_argument("--model-version", default=None, help="restrict to one model_version")
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ap.add_argument("--self-test", action="store_true", help="run the built-in self-test")
    args = ap.parse_args(argv)

    if args.db:
        gen = get_session()
        db = next(gen)
        try:
            rows = load_axis_rows(db, model_version=args.model_version)
        finally:
            gen.close()
        report = build_report(rows)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_markdown(report))
        return 0

    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())
