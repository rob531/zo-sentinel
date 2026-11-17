"""logic.py -- data and computation layer for the axis confidence profile service."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

CONFIDENT_THRESHOLD = 0.7
LOW_CONFIDENCE_THRESHOLD = 0.5


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _as_bool(value: Any) -> bool:
    return bool(value)


def _profile_for_rows(
    axis_name: str,
    rows: Iterable[Any],
) -> Dict[str, Any]:
    """Build one axis profile from ORM result rows."""
    count = 0
    servers = set()
    p_top_values: List[float] = []
    confident_count = 0
    low_confidence_count = 0
    escalated_count = 0
    last_scored_at = None

    for row in rows:
        count += 1
        if row.server_id is not None:
            servers.add(row.server_id)

        p_top = _as_float(row.p_top)
        if p_top is not None:
            p_top_values.append(p_top)
            if p_top >= CONFIDENT_THRESHOLD:
                confident_count += 1
            if p_top < LOW_CONFIDENCE_THRESHOLD:
                low_confidence_count += 1

        if _as_bool(row.escalated):
            escalated_count += 1

        if row.scored_at is not None:
            if last_scored_at is None or row.scored_at > last_scored_at:
                last_scored_at = row.scored_at

    mean_p_top = sum(p_top_values) / len(p_top_values) if p_top_values else 0.0
    median_p_top = float(median(p_top_values)) if p_top_values else 0.0

    return {
        "count": count,
        "server_count": len(servers),
        "mean_p_top": mean_p_top,
        "median_p_top": median_p_top,
        "confident_share": confident_count / count if count else 0.0,
        "low_confidence_share": low_confidence_count / count if count else 0.0,
        "escalated_share": escalated_count / count if count else 0.0,
        "last_scored_at": last_scored_at,
    }


def get_confidence_profile(
    db: Session,
    axis: Optional[str] = None,
    min_p_top: Optional[float] = None,
) -> Dict[str, Any]:
    """Return confidence statistics grouped by the valid axis names."""
    if axis is not None and axis not in VALID_AXES:
        raise ValueError(f"Unknown axis: {axis}")

    if min_p_top is not None and min_p_top < 0:
        raise ValueError("min_p_top must be non-negative")

    query = select(McpLlmAxisScore)
    if axis is not None:
        query = query.where(McpLlmAxisScore.axis_name == axis)
    if min_p_top is not None:
        query = query.where(McpLlmAxisScore.p_top >= min_p_top)

    rows = list(db.execute(query).scalars().all())
    rows_by_axis: Dict[str, List[Any]] = {name: [] for name in VALID_AXES}
    for row in rows:
        if row.axis_name in rows_by_axis:
            rows_by_axis[row.axis_name].append(row)

    requested_axes = (axis,) if axis is not None else VALID_AXES
    axes = {
        name: _profile_for_rows(name, rows_by_axis.get(name, []))
        for name in requested_axes
    }

    all_rows = [row for name in requested_axes for row in rows_by_axis.get(name, [])]
    server_ids = {
        row.server_id
        for row in all_rows
        if row.server_id is not None
    }

    return {
        "generated_at": datetime.now(timezone.utc),
        "thresholds": {
            "confident": CONFIDENT_THRESHOLD,
            "low_confidence": LOW_CONFIDENCE_THRESHOLD,
        },
        "axes": axes,
        "overall": {
            "count": len(all_rows),
            "server_count": len(server_ids),
        },
    }


def get_axis_confidence_profile(
    db: Session,
    axis: Optional[str] = None,
    min_p_top: Optional[float] = None,
) -> Dict[str, Any]:
    """Compatibility name for callers that use the axis-specific function name."""
    return get_confidence_profile(db, axis=axis, min_p_top=min_p_top)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id=101,
                    axis_name="auth_strength",
                    label="strong",
                    label_index=0,
                    p_top=0.9,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=False,
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id=102,
                    axis_name="auth_strength",
                    label="weak",
                    label_index=1,
                    p_top=0.3,
                    p_critical=0.2,
                    p_danger=0.2,
                    escalated=True,
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id=103,
                    axis_name="exploit_surface",
                    label="moderate",
                    label_index=0,
                    p_top=0.6,
                    p_critical=0.2,
                    p_danger=0.2,
                    escalated=False,
                    model_version="test-model",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/axes/confidence-profile")
    def confidence_profile_endpoint(
        axis: Optional[str] = None,
        min_p_top: Optional[float] = None,
        db: Session = Depends(get_session),
    ):
        return get_confidence_profile(db, axis=axis, min_p_top=min_p_top)

    response = TestClient(test_app).get("/api/axes/confidence-profile")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["axes"]["auth_strength"]["count"] == 2
    assert payload["axes"]["auth_strength"]["confident_share"] == 0.5
    assert payload["axes"]["exploit_surface"]["mean_p_top"] == 0.6
    print("PASS")