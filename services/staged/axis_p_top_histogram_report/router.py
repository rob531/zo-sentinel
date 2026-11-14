"""axis_p_top_histogram_report.py -- per-axis p_top histogram statistics.

Offline analysis / regression checks on the LLM scoring model's output
distribution: how the top-class probability (p_top) is spread across the 7
risk axes.

Two layers:
  * pure computation -- build_axis_histogram(rows, bins) / summarize_axis_histograms(rows, bins)
    take caller-supplied dicts and touch nothing else (stdlib only: statistics,
    math, collections -- no pandas/numpy, no DB, no network, no writes).
  * data seam -- fetch_axis_rows(db) reads the REAL app table through the REAL
    app session dependency (app.db.get_session) and app.models.McpLlmAxisScore,
    then hands the rows to the pure layer.

Row shape (mcp_llm_axis_scores-shaped):
    axis_name, p_top, p_critical, p_danger, label, label_index, scored_at
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/reports", tags=["reports"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

DEFAULT_BINS = 10
MIN_BINS = 2
MAX_BINS = 100


# ---------------------------------------------------------------- helpers


def _clamp_bins(bins: Any) -> int:
    """Histogram bucket count, clamped to [2, 100]."""
    try:
        n = int(bins)
    except (TypeError, ValueError):
        return DEFAULT_BINS
    return max(MIN_BINS, min(MAX_BINS, n))


def _coerce_p_top(row: Dict[str, Any]) -> Optional[float]:
    """p_top as a float in [0, 1]; None when missing/unparsable."""
    raw = row.get("p_top")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        p = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(p):
        return None
    return min(1.0, max(0.0, p))


def _percentile(values: List[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 1]) of an unsorted list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, int(math.ceil(q * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def _bin_index(value: float, bins: int) -> int:
    """Bucket index for a value in [0, 1] across `bins` equal-width buckets."""
    idx = int(value * bins)
    if idx < 0:
        idx = 0
    if idx >= bins:
        idx = bins - 1
    return idx


# ---------------------------------------------------------------- pure core


def build_axis_histogram(rows: List[Dict[str, Any]], bins: int = DEFAULT_BINS) -> Dict[str, Any]:
    """Histogram statistics for ONE axis.

    `rows` are mcp_llm_axis_scores-shaped dicts. Only rows sharing the first
    usable axis_name are considered (a single-axis report). Rows without a
    usable p_top are skipped. The input list is never mutated.
    """
    n_bins = _clamp_bins(bins)
    usable: List[Dict[str, Any]] = []
    axis_name = ""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if not axis:
            continue
        if not axis_name:
            axis_name = axis
        if axis != axis_name:
            continue
        p = _coerce_p_top(row)
        if p is None:
            continue
        usable.append({"p_top": p})

    values = [u["p_top"] for u in usable]
    count = len(values)
    counts = [0] * n_bins
    for v in values:
        counts[_bin_index(v, n_bins)] += 1

    return {
        "axis_name": axis_name,
        "count": count,
        "mean_p_top": (sum(values) / count) if count else 0.0,
        "median_p_top": statistics.median(values) if count else 0.0,
        "p90_p_top": _percentile(values, 0.90),
        "max_p_top": max(values) if count else 0.0,
        "bins": [
            {
                "lower": round(i / n_bins, 10),
                "upper": round((i + 1) / n_bins, 10),
                "count": counts[i],
            }
            for i in range(n_bins)
        ],
    }


def summarize_axis_histograms(rows: List[Dict[str, Any]],
                              bins: int = DEFAULT_BINS) -> Dict[str, Any]:
    """Per-axis histogram statistics for every axis present in `rows`.

    Returns {"axis_count", "row_count", "axes": [...]} with the per-axis dicts
    sorted by axis_name. Empty input returns axis_count 0 / row_count 0 /
    axes [] rather than raising.
    """
    n_bins = _clamp_bins(bins)
    grouped: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    total = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if not axis:
            continue
        total += 1
        grouped.setdefault(axis, []).append(row)

    axes = [build_axis_histogram(group, n_bins) for _axis, group in grouped.items()]
    axes.sort(key=lambda a: a.get("axis_name") or "")

    return {
        "axis_count": len(axes),
        "row_count": total,
        "axes": axes,
    }


# ---------------------------------------------------------------- data seam


def fetch_axis_rows(db: Session, axis_name: Optional[str] = None,
                    rows: int = 2000) -> List[Dict[str, Any]]:
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
            "p_top": r.p_top,
            "p_critical": r.p_critical,
            "p_danger": r.p_danger,
            "label": r.label,
            "label_index": r.label_index,
            "scored_at": r.scored_at,
        })
    return out


@router.get("/axis-p-top-histogram")
def axis_p_top_histogram(axis: Optional[str] = None, bins: int = DEFAULT_BINS,
                          db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Per-axis p_top histogram statistics from the REAL app table."""
    return summarize_axis_histograms(fetch_axis_rows(db, axis_name=axis), bins)


# ---------------------------------------------------------------- entrypoint


def main(argv: Optional[List[str]] = None) -> int:
    """Entrypoint: read the real table and print the histogram summary as JSON."""
    ap = argparse.ArgumentParser(description="Per-axis p_top histogram report")
    ap.add_argument("--axis", default=None, help="restrict to a single axis")
    ap.add_argument("--bins", type=int, default=DEFAULT_BINS, help="bucket count (2..100)")
    ap.add_argument("--rows", type=int, default=2000, help="max rows to read")
    args = ap.parse_args(argv)

    provider = get_session()
    db: Session = next(provider)
    try:
        fetched = fetch_axis_rows(db, axis_name=args.axis, rows=args.rows)
    finally:
        provider.close()

    print(json.dumps(summarize_axis_histograms(fetched, args.bins),
                     indent=2, sort_keys=True, default=str))
    return 0


# ---------------------------------------------------------------- self-test


def _self_test() -> int:
    # --- synthetic rows: 3 for overall_risk, 2 for auth_strength ---
    rows: List[Dict[str, Any]] = [
        {"axis_name": "overall_risk", "p_top": 0.10, "p_critical": 0.02,
         "p_danger": 0.03, "label": "LOW", "label_index": 0,
         "scored_at": "2026-10-30T00:00:00+00:00"},
        {"axis_name": "overall_risk", "p_top": 0.50, "p_critical": 0.11,
         "p_danger": 0.22, "label": "MEDIUM", "label_index": 1,
         "scored_at": "2026-10-30T00:01:00+00:00"},
        {"axis_name": "overall_risk", "p_top": 0.95, "p_critical": 0.44,
         "p_danger": 0.30, "label": "HIGH", "label_index": 2,
         "scored_at": "2026-10-30T00:02:00+00:00"},
        {"axis_name": "auth_strength", "p_top": 0.25, "p_critical": 0.05,
         "p_danger": 0.09, "label": "STRONG", "label_index": 1,
         "scored_at": "2026-10-30T00:03:00+00:00"},
        {"axis_name": "auth_strength", "p_top": 0.75, "p_critical": 0.20,
         "p_danger": 0.31, "label": "WEAK", "label_index": 2,
         "scored_at": "2026-10-30T00:04:00+00:00"},
    ]

    summary = summarize_axis_histograms(rows)
    assert summary["row_count"] == 5, summary
    assert summary["axis_count"] == 2, summary
    assert [a["axis_name"] for a in summary["axes"]] == ["auth_strength", "overall_risk"], summary

    overall = summary["axes"][1]
    expected_mean = (0.10 + 0.50 + 0.95) / 3.0
    assert abs(overall["mean_p_top"] - expected_mean) < 1e-9, overall
    assert overall["count"] == 3, overall
    assert sum(b["count"] for b in overall["bins"]) == overall["count"], overall
    assert abs(overall["max_p_top"] - 0.95) < 1e-9, overall
    assert abs(overall["median_p_top"] - 0.50) < 1e-9, overall
    assert len(overall["bins"]) == DEFAULT_BINS, overall

    auth = summary["axes"][0]
    assert abs(auth["mean_p_top"] - 0.50) < 1e-9, auth
    assert sum(b["count"] for b in auth["bins"]) == auth["count"], auth

    # --- single-axis build ---
    one = build_axis_histogram([r for r in rows if r["axis_name"] == "overall_risk"])
    assert one["axis_name"] == "overall_risk", one
    assert one["count"] == 3, one
    assert sum(b["count"] for b in one["bins"]) == 3, one

    # --- empty input must not raise ---
    empty = summarize_axis_histograms([])
    assert empty["axis_count"] == 0, empty
    assert empty["row_count"] == 0, empty
    assert empty["axes"] == [], empty
    assert build_axis_histogram([])["count"] == 0

    # --- bin count clamping + edge cases ---
    clamped = summarize_axis_histograms(rows, bins=1)
    assert len(clamped["axes"][0]["bins"]) == MIN_BINS, clamped
    clamped = summarize_axis_histograms(rows, bins=1000)
    assert len(clamped["axes"][0]["bins"]) == MAX_BINS, clamped

    junk = summarize_axis_histograms([{"axis_name": "overall_risk", "p_top": None},
                                      {"axis_name": "overall_risk", "p_top": "x"},
                                      {"axis_name": "", "p_top": 0.5}])
    assert junk["row_count"] == 2, junk   # unnamed-axis row dropped; unusable p_top skipped
    assert junk["axes"][0]["count"] == 0, junk
    assert junk["axes"][0]["axis_name"] == "overall_risk", junk

    # --- inputs are never mutated ---
    before = [dict(r) for r in rows]
    summarize_axis_histograms(rows)
    assert rows == before, rows

    # --- real data layer wiring (SQLite session over the REAL models) ---
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    try:
        s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                              label="HIGH", model_version="v3.0_40974559"))
        s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                              label="STRONG", model_version="v3.0_40974559"))
        s.commit()

        loaded = fetch_axis_rows(s)
        assert len(loaded) == 2, loaded
        assert loaded[0]["axis_name"] in ("overall_risk", "auth_strength"), loaded

        # --- endpoint over the REAL data layer (SQLite override of get_session) ---
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
        r = c.get("/api/reports/axis-p-top-histogram")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["axis_count"] == 2, body
        assert body["row_count"] == 2, body
        # seeded rows carry no p_top (column defaults to NULL) -> counts stay 0
        for axis_block in body["axes"]:
            assert axis_block["count"] == 0, body
            assert sum(b["count"] for b in axis_block["bins"]) == 0, body
        # a scored row flows through the same path with a real p_top
        s.execute(text("UPDATE mcp_llm_axis_scores SET p_top = 0.75 "
                       "WHERE axis_name = 'overall_risk'"))
        s.commit()
        r2 = c.get("/api/reports/axis-p-top-histogram")
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        overall = next(a for a in body2["axes"] if a["axis_name"] == "overall_risk")
        assert overall["count"] == 1, body2
        assert abs(overall["mean_p_top"] - 0.75) < 1e-9, body2
        assert sum(b["count"] for b in overall["bins"]) == 1, body2
    finally:
        s.close()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
