# deps: sqlalchemy
"""axis_p_top_calibration_report.py -- how the LLM axis model distributes its
top-class probability (p_top) across the 7 risk axes.

Read-only calibration report (no HTTP route, no DB writes): operators use it to
judge whether the SFT student model is over-confident on any axis.

Two layers:
  * pure computation -- compute_report(rows) -> dict, render_markdown(report)
    take rows from the caller and touch nothing else (no DB, no network).
  * data seam -- build_calibration_report(axis_name, rows) reads the REAL app
    table through the REAL app session dependency (app.db.get_session) and
    app.models.McpLlmAxisScore, then hands the rows to the pure layer.

Verdict rule:
  OVERCONFIDENT   mean_p_top >= 0.9 AND overconfidence_rate >= 0.15
  UNDERCONFIDENT  mean_p_top <= 0.5
  CALIBRATED      otherwise
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

N_DECILES = 10
N_BUCKETS = 10

# Relative severity of a label within an axis (higher = more risk). Used only
# to identify the LOWEST-risk class of an axis for the overconfidence measure.
SEVERITY = {
    "critical": 5, "danger": 4, "high": 4, "unsafe": 4, "unknown": 4,
    "weak": 3, "broad": 3, "external": 3, "moderate": 3, "medium": 3,
    "elevated": 3, "exposed": 3,
    "established": 2, "limited": 2, "nominal": 2, "low": 2, "contained": 2,
    "safe": 1, "strong": 1, "minimal": 1, "none": 1, "benign": 1, "isolated": 1,
}

DEFAULT_SEVERITY = 3


def _severity(label: Any) -> int:
    key = str(label or "").strip().lower()
    if not key:
        return DEFAULT_SEVERITY
    if key in SEVERITY:
        return SEVERITY[key]
    for word, sev in SEVERITY.items():
        if word in key:
            return sev
    return DEFAULT_SEVERITY


def _coerce_p_top(row: Dict[str, Any]) -> Optional[float]:
    raw = row.get("p_top")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        p = float(raw)
    except (TypeError, ValueError):
        return None
    if p != p:  # NaN
        return None
    return min(1.0, max(0.0, p))


def _as_escalated(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _deciles(values: List[float]) -> Dict[str, int]:
    """Equal-count deciles of the p_top distribution (d0 = lowest 10%)."""
    counts = {f"d{i}": 0 for i in range(N_DECILES)}
    n = len(values)
    if not n:
        return counts
    for i, _v in enumerate(sorted(values)):
        idx = (i * N_DECILES) // n
        if idx >= N_DECILES:
            idx = N_DECILES - 1
        counts[f"d{idx}"] += 1
    return counts


def _confidence_buckets(values: List[float]) -> List[Dict[str, Any]]:
    """Fixed 0.1-wide buckets over [0, 1] for the p_top distribution."""
    counts = [0] * N_BUCKETS
    for p in values:
        idx = int(p * N_BUCKETS)
        if idx >= N_BUCKETS:
            idx = N_BUCKETS - 1
        counts[idx] += 1
    return [{"bucket": f"{i / N_BUCKETS:.1f}-{(i + 1) / N_BUCKETS:.1f}", "count": counts[i]}
            for i in range(N_BUCKETS)]


def compute_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure computation: build the calibration report from caller-supplied rows."""
    usable: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if axis not in AXES:
            continue
        p = _coerce_p_top(row)
        if p is None:
            continue
        usable.append({
            "axis_name": axis,
            "label": row.get("label"),
            "p_top": p,
            "escalated": _as_escalated(row.get("escalated")),
        })

    n = len(usable)
    values = [u["p_top"] for u in usable]
    mean_p_top = sum(values) / n if n else 0.0
    median_p_top = _median(values)

    # lowest-risk class per axis
    min_sev: Dict[str, int] = {}
    for u in usable:
        sev = _severity(u["label"])
        cur = min_sev.get(u["axis_name"])
        if cur is None or sev < cur:
            min_sev[u["axis_name"]] = sev

    overconf_rows = 0
    for u in usable:
        if u["p_top"] >= 0.9 and _severity(u["label"]) <= min_sev.get(u["axis_name"], DEFAULT_SEVERITY):
            overconf_rows += 1
    overconfidence_rate = (overconf_rows / n) if n else 0.0

    escalated_count = sum(1 for u in usable if u["escalated"])

    if mean_p_top >= 0.9 and overconfidence_rate >= 0.15:
        verdict = "OVERCONFIDENT"
    elif mean_p_top <= 0.5:
        verdict = "UNDERCONFIDENT"
    else:
        verdict = "CALIBRATED"

    axis_names = sorted({u["axis_name"] for u in usable})
    return {
        "axis_name": axis_names[0] if len(axis_names) == 1 else None,
        "sample_size": n,
        "mean_p_top": round(mean_p_top, 6),
        "median_p_top": round(median_p_top, 6),
        "deciles": _deciles(values),
        "confidence_buckets": _confidence_buckets(values),
        "overconfidence_rate": round(overconfidence_rate, 6),
        "escalated_count": escalated_count,
        "verdict": verdict,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the report as a Markdown table of the confidence buckets + verdict."""
    report = report or {}
    deciles = report.get("deciles") or {}
    buckets = report.get("confidence_buckets") or []
    lines: List[str] = []
    lines.append("## Axis p_top calibration")
    lines.append("")
    lines.append(f"axis: {report.get('axis_name') or 'all'} | "
                 f"sample_size: {report.get('sample_size', 0)} | "
                 f"mean_p_top: {report.get('mean_p_top', 0.0)} | "
                 f"median_p_top: {report.get('median_p_top', 0.0)}")
    lines.append("")
    lines.append("| confidence bucket | count |")
    lines.append("| --- | --- |")
    for b in buckets:
        lines.append(f"| {b.get('bucket', '')} | {b.get('count', 0)} |")
    lines.append("")
    lines.append("| decile | count |")
    lines.append("| --- | --- |")
    for key in sorted(deciles):
        lines.append(f"| {key} | {deciles[key]} |")
    lines.append("")
    lines.append(f"overconfidence_rate: {report.get('overconfidence_rate', 0.0)} | "
                 f"escalated_count: {report.get('escalated_count', 0)}")
    lines.append("")
    lines.append(f"verdict: {report.get('verdict', 'CALIBRATED')}")
    return "\n".join(lines)


# --------------------------------------------------------------- data seam

def _fetch_rows(db: Session, axis_name: Optional[str] = None,
                rows: int = 500) -> List[Dict[str, Any]]:
    """Read the REAL axis scores (read-only) through the REAL app model."""
    stmt = select(McpLlmAxisScore)
    if axis_name:
        stmt = stmt.where(McpLlmAxisScore.axis_name == axis_name)
    stmt = stmt.order_by(McpLlmAxisScore.scored_at.desc())
    if rows and rows > 0:
        stmt = stmt.limit(int(rows))
    out: List[Dict[str, Any]] = []
    for r in db.execute(stmt).scalars().all():
        out.append({
            "server_id": r.server_id,
            "axis_name": r.axis_name,
            "label": r.label,
            "p_top": r.p_top,
            "p_critical": r.p_critical,
            "p_danger": r.p_danger,
            "escalated": r.escalated,
            "decision_rule_version": r.decision_rule_version,
            "model_version": r.model_version,
            "scored_at": r.scored_at,
        })
    return out


def build_calibration_report(axis_name: Optional[str] = None,
                             rows: int = 500) -> Dict[str, Any]:
    """Build the calibration report from the REAL app table (read-only)."""
    gen = get_session()
    db: Session = next(gen)
    try:
        fetched = _fetch_rows(db, axis_name=axis_name, rows=rows)
    finally:
        gen.close()
    return compute_report(fetched)


def run(argv: Optional[List[str]] = None) -> int:
    """Entrypoint: read the real table and print the markdown report."""
    ap = argparse.ArgumentParser(description="LLM axis p_top calibration report")
    ap.add_argument("--axis", default=None, help="restrict to a single axis")
    ap.add_argument("--rows", type=int, default=500, help="max rows to read")
    ap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = ap.parse_args(argv)

    report = build_calibration_report(axis_name=args.axis, rows=args.rows)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


# ---------------------------------------------------------------- self-test

def _self_test() -> int:
    # synthetic in-memory rows: 200 rows across the 7 real axes
    synthetic: List[Dict[str, Any]] = []
    for i in range(200):
        axis = AXES[i % len(AXES)]
        p = round(((i * 37) % 100) / 100.0, 4)
        label = "CRITICAL" if p >= 0.9 else ("HIGH" if p >= 0.7 else
                 ("MEDIUM" if p >= 0.4 else "LOW"))
        synthetic.append({"axis_name": axis, "label": label, "p_top": p,
                          "escalated": p >= 0.9})

    report = compute_report(synthetic)

    assert report["sample_size"] == 200, report["sample_size"]
    deciles = report["deciles"]
    assert len(deciles) == 10, deciles
    assert sum(deciles.values()) == report["sample_size"], deciles
    assert set(deciles) == {f"d{i}" for i in range(10)}, deciles
    assert 0.0 <= report["mean_p_top"] <= 1.0, report["mean_p_top"]
    assert 0.0 <= report["median_p_top"] <= 1.0, report["median_p_top"]
    assert report["verdict"] in ("CALIBRATED", "OVERCONFIDENT", "UNDERCONFIDENT"), report
    assert 0.0 <= report["overconfidence_rate"] <= 1.0, report
    assert len(report["confidence_buckets"]) == 10, report["confidence_buckets"]
    assert sum(b["count"] for b in report["confidence_buckets"]) == 200

    md = render_markdown(report)
    assert "verdict" in md, md
    assert "confidence bucket" in md, md

    # single-axis slice
    one = compute_report([r for r in synthetic if r["axis_name"] == "overall_risk"])
    assert one["axis_name"] == "overall_risk", one
    assert one["sample_size"] == len([r for r in synthetic if r["axis_name"] == "overall_risk"])
    assert sum(one["deciles"].values()) == one["sample_size"], one

    # edge case: empty sample
    empty = compute_report([])
    assert empty["sample_size"] == 0, empty
    assert empty["verdict"] in ("CALIBRATED", "OVERCONFIDENT", "UNDERCONFIDENT"), empty

    # real data layer wiring: read the real table through the real model
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
        for i, (ax, lbl, p) in enumerate((
                ("overall_risk", "HIGH", 0.91),
                ("auth_strength", "STRONG", 0.12),
                ("data_sensitivity", "CRITICAL", 0.77)), start=1):
            s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax, label=lbl,
                                  p_top=p, model_version="v3.0_40974559"))
        s.commit()
        loaded = _fetch_rows(s)
        assert len(loaded) == 3, loaded
        rep = compute_report(loaded)
        assert rep["sample_size"] == 3, rep
        assert "verdict" in render_markdown(rep)
    finally:
        s.close()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
