# deps: fastapi, sqlalchemy, pydantic
"""Scoring stats API -- per-axis aggregation of LLM axis scores with tier breakdown.

GET /api/scoring/stats
  Returns per-axis statistics (count, mean, median, p25, p75, min, max)
  and tier breakdown derived from p_top values.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import json
import statistics
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

router = APIRouter(prefix="/api", tags=["scoring_stats"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class AxisStats(BaseModel):
    count: int
    mean: float
    median: float
    p25: float
    p75: float
    min: float
    max: float
    tier_breakdown: dict[str, int] = Field(default_factory=dict)


class ScoringStatsResponse(BaseModel):
    generated_at: str
    axes: dict[str, AxisStats]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (Postgres percentile_cont equivalent)."""
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
    """Derive risk tier label from p_top probability."""
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


@router.get("/scoring/stats", response_model=ScoringStatsResponse)
def get_scoring_stats(
    session: Session = Depends(get_session),
) -> ScoringStatsResponse:
    """
    Aggregate per-axis statistics from mcp_llm_axis_scores.
    Computes count, mean, median, p25, p75, min, max per axis_name,
    plus a tier_breakdown derived from p_top on overall_risk rows.
    """
    rows = session.query(McpLlmAxisScore).all()

    # Group rows by axis_name
    axis_groups: dict[str, list[dict[str, Any]]] = {}
    tier_map: dict[str, list[dict[str, Any]]] = {}

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
            tier_map.setdefault(axis, []).append(raw)

    axes_stats: dict[str, AxisStats] = {}

    for axis_name, group in axis_groups.items():
        p_values = [r["p_top"] for r in group if r["p_top"] is not None]
        count = len(group)

        if not p_values:
            tb = {}
            for r in tier_map.get(axis_name, []):
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

        tb: dict[str, int] = {}
        for r in tier_map.get(axis_name, []):
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

    return ScoringStatsResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        axes=axes_stats,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import random
    import sys

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # Seed data
    with SessionLocal() as db:
        srv1_id = "srv-stats-1"
        srv2_id = "srv-stats-2"
        srv3_id = "srv-stats-3"

        now = datetime.utcnow()
        axes = ["overall_risk", "auth_strength", "capability_breadth"]

        seed_rows = [
            # overall_risk -- high p_top -> "low" tier
            (srv1_id, "overall_risk", 0.85, now),
            (srv2_id, "overall_risk", 0.78, now),
            (srv3_id, "overall_risk", 0.72, now),
            # medium p_top -> "medium" tier
            (srv1_id, "overall_risk", 0.45, now),
            # auth_strength axis
            (srv1_id, "auth_strength", 0.55, now),
            (srv2_id, "auth_strength", 0.62, now),
            (srv3_id, "auth_strength", 0.41, now),
            # capability_breadth axis
            (srv1_id, "capability_breadth", 0.30, now),
            (srv2_id, "capability_breadth", 0.91, now),
            # extra rows for median spread
            (srv1_id, "overall_risk", 0.60, now),
            (srv2_id, "overall_risk", 0.80, now),
            (srv3_id, "overall_risk", 0.50, now),
        ]

        for i, (srv_id, axis, p_top, scored) in enumerate(seed_rows):
            db.add(
                McpLlmAxisScore(
                    id=10_000 + i,
                    server_id=srv_id,
                    axis_name=axis,
                    label="test",
                    label_index=0,
                    # Vary model_version to avoid UNIQUE(server_id, axis_name, model_version)
                    model_version=f"m{i + 1}",
                    decision_rule_version="v1",
                    adapter_sha256="a" * 64,
                    probs={"HIGH": p_top},
                    p_top=p_top,
                    p_critical=round(p_top * 0.1, 4),
                    p_danger=round(p_top * 0.2, 4),
                    escalated=False,
                    scored_at=scored,
                )
            )
        db.commit()

    # Override dependency
    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Build test app -- use LOCAL FastAPI() per project convention
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_session

    client = TestClient(test_app)

    # Happy path: 200 + valid shape
    resp = client.get("/api/scoring/stats")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "generated_at" in data, "Missing generated_at"
    assert "axes" in data, "Missing axes"
    axes_out = data["axes"]
    assert isinstance(axes_out, dict) and axes_out, "axes must be non-empty dict"

    # Check overall_risk has tier_breakdown
    assert "overall_risk" in axes_out, f"Missing overall_risk axis: {list(axes_out.keys())}"
    or_stats = axes_out["overall_risk"]
    assert "tier_breakdown" in or_stats, "Missing tier_breakdown in overall_risk"
    assert isinstance(or_stats["tier_breakdown"], dict), "tier_breakdown must be dict"
    assert len(or_stats["tier_breakdown"]) > 0, "tier_breakdown must not be empty"

    # Check all axes have required stat fields
    required_stat_keys = {"count", "mean", "median", "p25", "p75", "min", "max", "tier_breakdown"}
    for ax_name, ax_data in axes_out.items():
        assert required_stat_keys.issubset(ax_data), (
            f"Axis '{ax_name}' missing keys: {required_stat_keys - set(ax_data)}"
        )
        assert isinstance(ax_data["count"], int), f"count not int for {ax_name}"
        assert isinstance(ax_data["mean"], (int, float)), f"mean not numeric for {ax_name}"
        assert isinstance(ax_data["tier_breakdown"], dict), f"tier_breakdown not dict for {ax_name}"

    # No-auth path: the endpoint is public, so no 401/403 on missing auth
    # (auth=public per directive)

    print("PASS")
    sys.exit(0)
