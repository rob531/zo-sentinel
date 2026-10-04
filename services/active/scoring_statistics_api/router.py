# deps: fastapi, sqlalchemy, pydantic
"""Scoring Statistics API -- aggregate statistics on LLM axis score data.

GET /api/scoring/statistics
  Returns per-axis statistics: count, mean, median, p25, p75, min, max,
  and a tier breakdown derived from p_top values.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on McpLlmAxisScore.
"""
from __future__ import annotations

import statistics
import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_statistics_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class AxisStats(BaseModel):
    count: int = Field(..., description="Number of axis score rows")
    mean: float = Field(..., description="Mean p_top for the axis")
    median: float = Field(..., description="Median p_top for the axis")
    p25: float = Field(..., description="25th-percentile p_top")
    p75: float = Field(..., description="75th-percentile p_top")
    min: float = Field(..., description="Minimum p_top")
    max: float = Field(..., description="Maximum p_top")
    tier_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Risk-tier counts derived from p_top on overall_risk rows"
    )


class ScoringStatisticsResponse(BaseModel):
    generated_at: str = Field(..., description="ISO timestamp of generation")
    axes: dict[str, AxisStats] = Field(
        ..., description="Per-axis aggregated statistics"
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile matching Postgres percentile_cont behaviour."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    idx = (n - 1) * p
    lower = int(idx)
    upper = lower + 1
    weight = idx - lower
    if upper >= n:
        return sorted_vals[-1]
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


def _derive_tier(p_top: float | None) -> str | None:
    """Derive risk-tier label from a p_top probability value."""
    if p_top is None:
        return None
    if p_top >= 0.7:
        return "low"
    elif p_top >= 0.4:
        return "medium"
    return "high"


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/scoring/statistics", response_model=ScoringStatisticsResponse)
def get_scoring_statistics(
    session: Session = Depends(get_session),
) -> ScoringStatisticsResponse:
    """
    Aggregate per-axis statistics from mcp_llm_axis_scores.
    Computes count, mean, median, p25, p75, min, max per axis_name,
    plus a tier_breakdown derived from p_top on overall_risk rows.
    """
    rows = session.query(McpLlmAxisScore).all()

    # Group rows by axis_name
    axis_groups: dict[str, list[dict[str, Any]]] = {}
    overall_risk_rows: list[dict[str, Any]] = []

    for row in rows:
        raw = {
            "id": row.id,
            "server_id": row.server_id,
            "axis_name": row.axis_name,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
            "label": row.label,
            "label_index": row.label_index,
            "model_version": row.model_version,
            "decision_rule_version": row.decision_rule_version,
            "adapter_sha256": row.adapter_sha256,
            "escalated": row.escalated,
            "escalated_to": row.escalated_to,
            "scored_at": row.scored_at,
        }
        axis = row.axis_name or "unknown"
        axis_groups.setdefault(axis, []).append(raw)
        if axis == "overall_risk":
            overall_risk_rows.append(raw)

    axes_stats: dict[str, AxisStats] = {}

    for axis_name, group in axis_groups.items():
        p_values = [r["p_top"] for r in group if r["p_top"] is not None]
        count = len(group)

        if not p_values:
            tb: dict[str, int] = {}
            for r in overall_risk_rows:
                t = _derive_tier(r["p_top"])
                if t:
                    tb[t] = tb.get(t, 0) + 1
            axes_stats[axis_name] = AxisStats(
                count=count, mean=0.0, median=0.0,
                p25=0.0, p75=0.0, min=0.0, max=0.0,
                tier_breakdown=tb,
            )
            continue

        sorted_vals = sorted(p_values)

        tb = {}
        for r in overall_risk_rows:
            t = _derive_tier(r["p_top"])
            if t:
                tb[t] = tb.get(t, 0) + 1

        axes_stats[axis_name] = AxisStats(
            count=count,
            mean=round(statistics.mean(sorted_vals), 4),
            median=round(statistics.median(sorted_vals), 4),
            p25=round(_percentile(sorted_vals, 0.25), 4),
            p75=round(_percentile(sorted_vals, 0.75), 4),
            min=round(min(sorted_vals), 4),
            max=round(max(sorted_vals), 4),
            tier_breakdown=tb,
        )

    return ScoringStatisticsResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        axes=axes_stats,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime(2025, 1, 15, 12, 0, 0)

    # Seed: 3 axes, 8 rows
    # overall_risk: 5 rows -> tiers: high(<0.4), medium(0.4-0.7), low(>=0.7)
    # auth_strength: 2 rows
    # capability_breadth: 1 row
    seed_rows = [
        McpLlmAxisScore(
            id=1, server_id="srv-a", axis_name="overall_risk",
            model_version="mv1", label="HIGH", label_index=2,
            p_top=0.25, p_critical=0.1, p_danger=0.2,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="a" * 64,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2, server_id="srv-b", axis_name="overall_risk",
            model_version="mv2", label="MEDIUM", label_index=1,
            p_top=0.55, p_critical=0.2, p_danger=0.3,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="b" * 64,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=3, server_id="srv-c", axis_name="overall_risk",
            model_version="mv3", label="LOW", label_index=0,
            p_top=0.75, p_critical=0.05, p_danger=0.1,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="c" * 64,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=4, server_id="srv-d", axis_name="overall_risk",
            model_version="mv4", label="LOW", label_index=0,
            p_top=0.82, p_critical=0.02, p_danger=0.05,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="d" * 64,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=5, server_id="srv-e", axis_name="overall_risk",
            model_version="mv5", label="HIGH", label_index=2,
            p_top=0.38, p_critical=0.15, p_danger=0.25,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="e" * 64,
            scored_at=now,
        ),
        # auth_strength: p_top values [0.40, 0.60]
        McpLlmAxisScore(
            id=6, server_id="srv-a", axis_name="auth_strength",
            model_version="mv1", label="MEDIUM", label_index=1,
            p_top=0.40, p_critical=0.1, p_danger=0.2,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="a" * 64,
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=7, server_id="srv-b", axis_name="auth_strength",
            model_version="mv2", label="MEDIUM", label_index=1,
            p_top=0.60, p_critical=0.2, p_danger=0.3,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="b" * 64,
            scored_at=now,
        ),
        # capability_breadth: p_top [0.70]
        McpLlmAxisScore(
            id=8, server_id="srv-a", axis_name="capability_breadth",
            model_version="mv1", label="LOW", label_index=0,
            p_top=0.70, p_critical=0.05, p_danger=0.1,
            probs={}, escalated=False, escalated_to=None,
            decision_rule_version="v1", adapter_sha256="a" * 64,
            scored_at=now,
        ),
    ]

    with SessionLocal() as db:
        for row in seed_rows:
            db.add(row)
        db.commit()

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_session

    client = TestClient(test_app)

    # Happy path
    resp = client.get("/api/scoring/statistics")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "generated_at" in data, "Missing generated_at"
    assert "axes" in data, "Missing axes"
    axes_out = data["axes"]
    assert isinstance(axes_out, dict) and axes_out, "axes must be a non-empty dict"

    # overall_risk must exist and have tier_breakdown
    assert "overall_risk" in axes_out, f"Missing overall_risk axis: {list(axes_out.keys())}"
    or_stats = axes_out["overall_risk"]
    assert "tier_breakdown" in or_stats, "Missing tier_breakdown"
    tb = or_stats["tier_breakdown"]
    assert isinstance(tb, dict), "tier_breakdown must be a dict"
    # p_top: 0.25(high), 0.55(medium), 0.75(low), 0.82(low), 0.38(high)
    # tier_breakdown expected: high=2, medium=1, low=2
    assert tb.get("high") == 2, f"Expected high=2, got {tb.get('high')}"
    assert tb.get("medium") == 1, f"Expected medium=1, got {tb.get('medium')}"
    assert tb.get("low") == 2, f"Expected low=2, got {tb.get('low')}"

    # All required stat keys present
    required_stat_keys = {"count", "mean", "median", "p25", "p75", "min", "max", "tier_breakdown"}
    for ax_name, ax_data in axes_out.items():
        assert required_stat_keys.issubset(ax_data), (
            f"Axis '{ax_name}' missing keys: {required_stat_keys - set(ax_data)}"
        )
        assert isinstance(ax_data["count"], int), f"count not int for {ax_name}"
        assert isinstance(ax_data["mean"], (int, float)), f"mean not numeric for {ax_name}"

    # Check overall_risk stats are correct
    # p_top values: [0.25, 0.38, 0.55, 0.75, 0.82]
    assert or_stats["count"] == 5, f"count expected 5, got {or_stats['count']}"
    assert abs(or_stats["mean"] - 0.55) < 0.01, f"mean expected ~0.55, got {or_stats['mean']}"
    assert abs(or_stats["median"] - 0.55) < 0.01, f"median expected ~0.55, got {or_stats['median']}"
    assert abs(or_stats["min"] - 0.25) < 0.01, f"min expected 0.25, got {or_stats['min']}"
    assert abs(or_stats["max"] - 0.82) < 0.01, f"max expected 0.82, got {or_stats['max']}"

    # auth_strength: 2 rows, p_top [0.40, 0.60]
    as_stats = axes_out["auth_strength"]
    assert as_stats["count"] == 2, f"auth_strength count expected 2, got {as_stats['count']}"
    assert abs(as_stats["mean"] - 0.5) < 0.01, f"auth_strength mean expected ~0.5, got {as_stats['mean']}"
    assert abs(as_stats["median"] - 0.5) < 0.01, f"auth_strength median expected ~0.5, got {as_stats['median']}"

    # Public endpoint: no auth required, so no 401/403 expected
    print("PASS")
    sys.exit(0)
