"""Data and computation layer for the app3t axis variance API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def _percentile(values: List[float], percentile: float) -> float:
    """Return a linearly interpolated percentile for a non-empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])

    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def get_axis_variance(
    db: Session,
    days: int = 30,
    axis: Optional[str] = None,
    limit: int = 200,
    *,
    axis_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute axis-level risk spread over the configured scoring window."""
    selected_axis = axis if axis is not None else axis_name
    if selected_axis is not None and selected_axis not in AXIS_NAMES:
        return {"days": days, "axes": []}

    window_days = max(1, int(days))
    row_limit = max(1, min(int(limit), 1000))
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.escalated,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(row_limit)
    )
    if selected_axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == selected_axis)

    rows = db.execute(statement).all()

    grouped: Dict[str, Dict[str, Any]] = {
        name: {
            "axis_name": name,
            "label": name,
            "n_scored": 0,
            "values": [],
            "escalated_count": 0,
            "tier_buckets": {},
        }
        for name in AXIS_NAMES
    }

    for row in rows:
        name = row.axis_name
        if name not in grouped:
            continue

        bucket = grouped[name]
        bucket["n_scored"] += 1
        if row.label:
            bucket["label"] = row.label
        if row.p_top is not None:
            bucket["values"].append(float(row.p_top))
        if bool(row.escalated):
            bucket["escalated_count"] += 1

        tier = row.risk_tier or "unknown"
        bucket["tier_buckets"][tier] = bucket["tier_buckets"].get(tier, 0) + 1

    output_axes: List[Dict[str, Any]] = []
    names = (selected_axis,) if selected_axis is not None else AXIS_NAMES
    for name in names:
        bucket = grouped[name]
        values = bucket.pop("values")
        mean = sum(values) / len(values) if values else 0.0
        output_axes.append(
            {
                "axis_name": bucket["axis_name"],
                "label": bucket["label"],
                "n_scored": bucket["n_scored"],
                "mean_p_top": mean,
                "p50": _percentile(values, 0.50),
                "p90": _percentile(values, 0.90),
                "max_p_top": max(values) if values else 0.0,
                "escalated_count": bucket["escalated_count"],
                "tier_buckets": bucket["tier_buckets"],
            }
        )

    return {"days": window_days, "axes": output_axes}


def compute_axis_variance(
    db: Session,
    days: int = 30,
    axis: Optional[str] = None,
    limit: int = 200,
    *,
    axis_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility alias for callers using the computation-oriented name."""
    return get_axis_variance(
        db,
        days=days,
        axis=axis,
        limit=limit,
        axis_name=axis_name,
    )


def _self_test() -> None:
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.metadata.create_all(engine)

    now = datetime.now(timezone.utc) - timedelta(hours=1)
    servers = [
        McpServerRegistry(server_id="server-a", registry_source="test", risk_tier="high"),
        McpServerRegistry(server_id="server-b", registry_source="test", risk_tier="low"),
        McpServerRegistry(server_id="server-c", registry_source="test", risk_tier="low"),
    ]

    with Session(engine) as db:
        db.add_all(servers)
        for index, server_id in enumerate(("server-a", "server-b", "server-c"), start=1):
            for axis_index, axis_name in enumerate(AXIS_NAMES, start=1):
                db.add(
                    McpLlmAxisScore(
                        id=index * 100 + axis_index,
                        adapter_sha256=f"adapter-{index}",
                        axis_name=axis_name,
                        decision_rule_version="test",
                        escalated=index == 1,
                        escalated_to=None,
                        label=axis_name.replace("_", " ").title(),
                        label_index=axis_index,
                        model_version="test-model",
                        p_critical=0.1,
                        p_danger=0.2,
                        p_top=0.2 * index,
                        probs=None,
                        scored_at=now,
                        server_id=server_id,
                    )
                )
        db.commit()

        test_app = FastAPI()

        @test_app.get("/api/axis/variance")
        def variance_endpoint(
            days: int = 30,
            axis: Optional[str] = None,
            limit: int = 200,
            db: Session = Depends(get_session),
        ):
            return get_axis_variance(db, days=days, axis=axis, limit=limit)

        def override_session():
            yield db

        test_app.dependency_overrides[get_session] = override_session
        result = get_axis_variance(db)
        assert len(result["axes"]) == 7
        assert all(item["n_scored"] == 3 for item in result["axes"])
        assert all(abs(item["mean_p_top"] - 0.4) < 1e-9 for item in result["axes"])
        assert all(item["escalated_count"] == 1 for item in result["axes"])
        assert result["axes"][0]["tier_buckets"] == {"high": 1, "low": 2}

    print("PASS")


if __name__ == "__main__":
    _self_test()