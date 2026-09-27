# deps: fastapi, sqlalchemy, pydantic
"""Axis Label Aggregate API.

GET /api/axis-labels
    Returns all distinct axis_name values with label distribution per axis:
    label, label_index, count, avg p_top, avg p_critical, avg p_danger, escalated_count.

GET /api/axis-labels/{axis_name}
    Returns the label distribution for a specific axis_name.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_label_aggregate"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class LabelStatRow(BaseModel):
    label: str
    label_index: int
    count: int
    avg_p_top: float = Field(..., ge=0.0, le=1.0)
    avg_p_critical: float = Field(..., ge=0.0, le=1.0)
    avg_p_danger: float = Field(..., ge=0.0, le=1.0)
    escalated_count: int = 0


class AxisLabelDistribution(BaseModel):
    axis_name: str
    total_rows: int
    distinct_labels: int
    labels: list[LabelStatRow]


class AxisListItem(BaseModel):
    axis_name: str
    total_rows: int
    distinct_labels: int


class AxisListResponse(BaseModel):
    axes: list[AxisListItem]
    total_axes: int


# --------------------------------------------------------------------------- #
# Endpoint: list all axes
# --------------------------------------------------------------------------- #


@router.get("/axis-labels", response_model=AxisListResponse)
def list_all_axes(
    session: Session = Depends(get_session),
) -> AxisListResponse:
    """Return all distinct axis_name values with aggregate stats."""
    # Subquery to count escalated per (axis_name, label)
    escalated_sub = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            func.count().label("escalated_count"),
        )
        .where(McpLlmAxisScore.escalated == True)  # noqa: E712
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        .subquery()
    )

    # Aggregate per axis
    rows = (
        session.execute(
            select(
                McpLlmAxisScore.axis_name,
                func.count(McpLlmAxisScore.id).label("total_rows"),
                func.count(func.distinct(McpLlmAxisScore.label)).label("distinct_labels"),
            )
            .group_by(McpLlmAxisScore.axis_name)
            .order_by(McpLlmAxisScore.axis_name)
        )
        .all()
    )

    axes = [
        AxisListItem(
            axis_name=r.axis_name,
            total_rows=r.total_rows,
            distinct_labels=r.distinct_labels,
        )
        for r in rows
    ]

    return AxisListResponse(axes=axes, total_axes=len(axes))


# --------------------------------------------------------------------------- #
# Endpoint: label breakdown for a specific axis
# --------------------------------------------------------------------------- #


@router.get(
    "/axis-labels/{axis_name}",
    response_model=AxisLabelDistribution,
    name="axis_labels:get_by_axis",
)
def get_axis_labels(
    axis_name: str,
    session: Session = Depends(get_session),
) -> AxisLabelDistribution:
    """Return label distribution for a specific axis_name."""
    # Count escalated per label
    escalated_q = (
        select(
            McpLlmAxisScore.label,
            func.count().label("escalated_count"),
        )
        .where(
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.escalated == True,  # noqa: E712
        )
        .group_by(McpLlmAxisScore.label)
    )
    escalated_map: dict[str, int] = {
        r.label: r.escalated_count for r in session.execute(escalated_q).all()
    }

    # Aggregate per label
    agg_q = (
        select(
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            func.count(McpLlmAxisScore.id).label("count"),
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
            func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
            func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
        )
        .where(McpLlmAxisScore.axis_name == axis_name)
        .group_by(McpLlmAxisScore.label, McpLlmAxisScore.label_index)
        .order_by(McpLlmAxisScore.label_index, McpLlmAxisScore.label)
    )
    agg_rows = session.execute(agg_q).all()

    if not agg_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No rows found for axis_name={axis_name!r}",
        )

    total_rows = sum(r.count for r in agg_rows)

    labels = [
        LabelStatRow(
            label=r.label or "",
            label_index=r.label_index or 0,
            count=r.count,
            avg_p_top=round(float(r.avg_p_top or 0.0), 4),
            avg_p_critical=round(float(r.avg_p_critical or 0.0), 4),
            avg_p_danger=round(float(r.avg_p_danger or 0.0), 4),
            escalated_count=escalated_map.get(r.label or "", 0),
        )
        for r in agg_rows
    ]

    return AxisLabelDistribution(
        axis_name=axis_name,
        total_rows=total_rows,
        distinct_labels=len(labels),
        labels=labels,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    now = datetime.now(timezone.utc)
    rows = [
        # overall_risk axis
        McpLlmAxisScore(
            id=1, server_id="s1", axis_name="overall_risk",
            label="CRITICAL", label_index=0,
            p_top=0.92, p_critical=0.88, p_danger=0.10,
            escalated=True, model_version="m1",
            decision_rule_version="v1", adapter_sha256="a" * 64, scored_at=now,
        ),
        McpLlmAxisScore(
            id=2, server_id="s2", axis_name="overall_risk",
            label="CRITICAL", label_index=0,
            p_top=0.95, p_critical=0.90, p_danger=0.08,
            escalated=True, model_version="m2",
            decision_rule_version="v1", adapter_sha256="a" * 64, scored_at=now,
        ),
        McpLlmAxisScore(
            id=3, server_id="s3", axis_name="overall_risk",
            label="LOW", label_index=2,
            p_top=0.10, p_critical=0.05, p_danger=0.50,
            escalated=False, model_version="m3",
            decision_rule_version="v1", adapter_sha256="a" * 64, scored_at=now,
        ),
        # auth_strength axis
        McpLlmAxisScore(
            id=4, server_id="s1", axis_name="auth_strength",
            label="STRONG", label_index=1,
            p_top=0.80, p_critical=0.20, p_danger=0.30,
            escalated=False, model_version="m4",
            decision_rule_version="v1", adapter_sha256="a" * 64, scored_at=now,
        ),
        McpLlmAxisScore(
            id=5, server_id="s2", axis_name="auth_strength",
            label="WEAK", label_index=0,
            p_top=0.30, p_critical=0.40, p_danger=0.60,
            escalated=True, model_version="m5",
            decision_rule_version="v1", adapter_sha256="a" * 64, scored_at=now,
        ),
    ]

    with TestSession() as sess:
        for r in rows:
            sess.add(r)
        sess.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override
    client = TestClient(test_app)

    # T1: list all axes
    resp = client.get("/api/axis-labels")
    assert resp.status_code == 200, f"T1: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["total_axes"] == 2, f"T1 total_axes={data['total_axes']}"
    names = {a["axis_name"] for a in data["axes"]}
    assert names == {"auth_strength", "overall_risk"}, f"T1 names={names}"

    # T2: get specific axis
    resp = client.get("/api/axis-labels/overall_risk")
    assert resp.status_code == 200, f"T2: {resp.status_code} {resp.text}"
    d = resp.json()
    assert d["axis_name"] == "overall_risk"
    assert d["total_rows"] == 3
    assert d["distinct_labels"] == 2
    labels = {lbl["label"]: lbl for lbl in d["labels"]}
    crit = labels["CRITICAL"]
    assert crit["count"] == 2, f"CRITICAL count={crit['count']}"
    assert crit["avg_p_top"] > 0.9, f"avg_p_top={crit['avg_p_top']}"
    assert crit["escalated_count"] == 2, f"escalated_count={crit['escalated_count']}"
    low = labels["LOW"]
    assert low["escalated_count"] == 0, f"escalated_count LOW={low['escalated_count']}"

    # T3: 404 for unknown axis
    resp = client.get("/api/axis-labels/nonexistent_axis")
    assert resp.status_code == 404, f"T3: {resp.status_code}"

    # T4: labels sorted by label_index
    resp = client.get("/api/axis-labels/auth_strength")
    assert resp.status_code == 200, f"T4: {resp.status_code}"
    d = resp.json()
    idxs = [l["label_index"] for l in d["labels"]]
    assert idxs == sorted(idxs), f"T4 not sorted: {idxs}"

    # T5: empty DB returns empty axes list
    empty_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=empty_engine)
    EmptySession = sessionmaker(bind=empty_engine)

    def _empty_override():
        sess = EmptySession()
        try:
            yield sess
        finally:
            sess.close()

    empty_app = FastAPI()
    empty_app.include_router(router)
    empty_app.dependency_overrides[get_session] = _empty_override
    empty_client = TestClient(empty_app)
    resp = empty_client.get("/api/axis-labels")
    assert resp.status_code == 200, f"T5: {resp.status_code}"
    assert resp.json()["total_axes"] == 0

    print("PASS")
    sys.exit(0)
