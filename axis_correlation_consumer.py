#!/usr/bin/env python3
"""
Axis Correlation Scoring Consumer.

Reads axis score rows for a scored population, computes Pearson correlation between
each axis pair across servers, and returns per-server axis_contribution with
mean/std/rank per axis plus the full correlation matrix.

Interface: compute_score(metadata: dict) -> tuple[float, dict]
  metadata keys:
    - server_id (str): the target server
    - axis_scores (list[dict]): list of {"server_id", "axis_name", "p_top"} rows
      from mcp_llm_axis_scores for the current model_version

Returns (composite_score 0-100, evidence dict with per_axis stats + correlation_matrix).
Pure function: no DB writes, no network. Stdlib + typing only.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple, Any

AXES = (
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
)


def _pearson(xs: List[float], ys: List[float]) -> float:
    """Pearson correlation coefficient. Returns 0.0 when no variance."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    num = sum(a * b for a, b in zip(dx, dy))
    vx = math.sqrt(sum(a * a for a in dx))
    vy = math.sqrt(sum(b * b for b in dy))
    if vx < 1e-12 and vy < 1e-12:
        return 1.0   # both constant and identical -> perfectly correlated
    if vx < 1e-12 or vy < 1e-12:
        return 0.0
    return num / (vx * vy)


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Compute per-axis statistics and inter-axis Pearson correlation matrix
    for the scored population. Returns a composite_score + evidence dict.

    Args:
        metadata: dict with keys:
            - server_id (str): target server for the axis_contribution result
            - axis_scores (list[dict]): rows from mcp_llm_axis_scores, each dict
              MUST contain server_id (str), axis_name (str), p_top (float)

    Returns:
        (composite_score: float 0-100,
         evidence: dict with per_axis stats, correlation_matrix, axis_contribution)
    """
    server_id: str = metadata.get("server_id", "")
    axis_scores: List[Dict[str, Any]] = metadata.get("axis_scores", [])

    # Group by server_id -> {axis_name: p_top}
    by_server: Dict[str, Dict[str, float]] = {}
    for row in axis_scores:
        sid = row.get("server_id", "")
        ax = row.get("axis_name", "")
        pt = row.get("p_top")
        if not ax or pt is None:
            continue
        by_server.setdefault(sid, {})[ax] = float(pt)

    if not by_server:
        return 0.0, {
            "signal_type": "axis_correlation",
            "axis_contribution": {},
            "correlation_matrix": {},
            "per_axis_stats": {},
            "population_count": 0,
        }

    # Per-axis stats across population: mean_p_top, std_p_top, rank
    axis_names = [a for a in AXES if a in {r.get("axis_name") for r in axis_scores}]
    axis_means: Dict[str, float] = {}
    for ax in axis_names:
        vals = [by_server[s].get(ax) for s in by_server if ax in by_server[s]]
        axis_means[ax] = (sum(vals) / len(vals)) if vals else 0.0

    axis_stds: Dict[str, float] = {}
    for ax in axis_names:
        vals = [by_server[s].get(ax) for s in by_server if ax in by_server[s]]
        m = axis_means[ax]
        if len(vals) > 1:
            variance = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
            axis_stds[ax] = math.sqrt(variance)
        else:
            axis_stds[ax] = 0.0

    # Rank by mean_p_top descending (1 = highest)
    sorted_axes = sorted(axis_names, key=lambda a: axis_means[a], reverse=True)
    axis_ranks: Dict[str, int] = {ax: rank + 1 for rank, ax in enumerate(sorted_axes)}

    per_axis_stats: Dict[str, Dict[str, Any]] = {}
    for ax in axis_names:
        per_axis_stats[ax] = {
            "mean_p_top": round(axis_means[ax], 6),
            "std_p_top": round(axis_stds[ax], 6),
            "rank": axis_ranks[ax],
        }

    # Correlation matrix: all pairs of axes
    correlation_matrix: Dict[str, Dict[str, float]] = {}
    for ax_i in axis_names:
        correlation_matrix[ax_i] = {}
        for ax_j in axis_names:
            if ax_i == ax_j:
                correlation_matrix[ax_i][ax_j] = 1.0
            else:
                pairs = [
                    (by_server[s][ax_i], by_server[s][ax_j])
                    for s in by_server
                    if ax_i in by_server[s] and ax_j in by_server[s]
                ]
                if len(pairs) >= 2:
                    corr = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
                else:
                    corr = 0.0
                correlation_matrix[ax_i][ax_j] = round(corr, 6)

    # axis_contribution for the target server
    target_axes = by_server.get(server_id, {})
    axis_contribution: Dict[str, Dict[str, Any]] = {}
    for ax in axis_names:
        axis_contribution[ax] = {
            "p_top": target_axes.get(ax),
            "mean_p_top": round(axis_means[ax], 6),
            "std_p_top": round(axis_stds[ax], 6),
            "rank": axis_ranks[ax],
            "deviation_from_mean": (
                round(target_axes.get(ax, 0.0) - axis_means[ax], 6)
                if ax in target_axes else None
            ),
        }

    # Composite score: weighted sum of inverse-rank * mean_p_top, normalised
    composite = 0.0
    for ax in axis_names:
        composite += (1.0 / axis_ranks[ax]) * axis_means[ax]
    max_composite = sum(1.0 / r for r in range(1, len(axis_names) + 1)) if axis_names else 1.0
    composite_score = round(min(100.0, (composite / max_composite) * 100.0), 2)

    return composite_score, {
        "signal_type": "axis_correlation",
        "axis_contribution": axis_contribution,
        "correlation_matrix": correlation_matrix,
        "per_axis_stats": per_axis_stats,
        "population_count": len(by_server),
        "target_server": server_id,
    }


if __name__ == "__main__":
    # Self-test: 10 rows across 3 servers with known p_top values.
    # Asserts:
    #   1. Pearson(identical series, identical series) == 1.0
    #   2. Rank order matches mean_p_top descending
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import get_session
    from app.models import Base, McpLlmAxisScore

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed 10 rows: srv_a has p_top [0.9,0.7,...], srv_b [0.8,0.6,...], srv_c [0.7,0.5,...]
    MV = "v3.0_40974559"
    seed = [
        # srv_a  -- id 1-7
        (1, "srv_a", "overall_risk",       0.9, MV),
        (2, "srv_a", "auth_strength",     0.7, MV),
        (3, "srv_a", "capability_breadth",0.8, MV),
        (4, "srv_a", "data_sensitivity",  0.85,MV),
        (5, "srv_a", "network_egress",    0.6, MV),
        (6, "srv_a", "maintainer_trust",  0.75,MV),
        (7, "srv_a", "exploit_surface",   0.65,MV),
        # srv_b  -- id 8-14
        (8,  "srv_b", "overall_risk",     0.8,  MV),
        (9,  "srv_b", "auth_strength",     0.6,  MV),
        (10, "srv_b", "capability_breadth",0.7, MV),
        # srv_c  -- id 15-17 (partial, still valid for correlation)
        (15, "srv_c", "overall_risk",     0.7,  MV),
        (16, "srv_c", "auth_strength",    0.5,  MV),
        (17, "srv_c", "capability_breadth",0.6, MV),
    ]
    s = TS()
    for (idx, sid, ax, pt, mv) in seed:
        s.add(McpLlmAxisScore(id=idx, server_id=sid, axis_name=ax,
                              label="HIGH" if pt > 0.5 else "LOW",
                              p_top=pt, model_version=mv))
    s.commit(); s.close()

    # Read all rows from test session
    session = TS()
    rows = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.model_version == MV
    ).all()
    axis_scores = [
        {"server_id": r.server_id, "axis_name": r.axis_name, "p_top": r.p_top}
        for r in rows
    ]
    session.close()

    # Compute with srv_a as target
    score, evidence = compute_score({
        "server_id": "srv_a",
        "axis_scores": axis_scores,
    })

    # --- Test 1: identical-axis series correlation must be 1.0 ---
    # Two servers, same p_top for each axis -> perfect correlation
    test_rows = [
        {"server_id": "srv_test1", "axis_name": "overall_risk",      "p_top": 0.5},
        {"server_id": "srv_test2", "axis_name": "overall_risk",      "p_top": 0.5},
        {"server_id": "srv_test1", "axis_name": "exploit_surface",   "p_top": 0.5},
        {"server_id": "srv_test2", "axis_name": "exploit_surface",   "p_top": 0.5},
    ]
    _, test_evidence = compute_score({
        "server_id": "srv_test1",
        "axis_scores": test_rows,
    })
    corr = test_evidence["correlation_matrix"]["overall_risk"]["exploit_surface"]
    assert abs(corr - 1.0) < 1e-9, f"Identical series corr must be 1.0, got {corr}"

    # --- Test 2: rank order must match mean_p_top descending ---
    stats = evidence["per_axis_stats"]
    ranked = sorted(stats.items(), key=lambda x: x[1]["rank"])
    means_desc = sorted(stats.values(), key=lambda v: v["mean_p_top"], reverse=True)
    for (ax, st), expected in zip(ranked, means_desc):
        assert st["mean_p_top"] == expected["mean_p_top"], \
            f"Rank order mismatch: {ax} has mean {st['mean_p_top']} vs expected {expected['mean_p_top']}"

    # --- Test 3: composite_score in [0, 100] ---
    assert 0.0 <= score <= 100.0, f"composite_score {score} out of range"

    print("PASS")
