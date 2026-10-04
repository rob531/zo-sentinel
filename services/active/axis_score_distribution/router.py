# deps: fastapi, sqlalchemy, pydantic
"""Axis Score Distribution API -- reports distribution of LLM axis scores.

GET /api/axis_score_distribution/
  Returns distribution statistics for LLM axis scores (per-axis breakdowns,
  probability distributions, tier counts).

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Ensure repo root on path for app.* imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_score_distribution"])


# --- Response models --------------------------------------------------------

class AxisBucket(BaseModel):
    label: str = Field(..., description="Axis label (e.g. critical, danger, elevated)")
    label_index: int = Field(..., description="Ordinal position of the label")
    count: int = Field(..., description="Number of scores in this bucket")
    pct: float = Field(..., ge=0, le=100, description="Percentage of total")


class AxisDistribution(BaseModel):
    axis_name: str = Field(..., description="Name of the axis (e.g. overall_risk)")
    total_scores: int = Field(..., description="Total number of scored servers")
    buckets: List[AxisBucket] = Field(..., description="Distribution across label buckets")
    escalated_count: int = Field(..., description="Number of escalated scores")
    avg_p_top: Optional[float] = Field(None, description="Average p_top across scores")
    avg_p_critical: Optional[float] = Field(None, description="Average p_critical across scores")
    avg_p_danger: Optional[float] = Field(None, description="Average p_danger across scores")


class OverallDistribution(BaseModel):
    total_servers_scored: int
    by_axis: List[AxisDistribution]
    scored_at: str = Field(..., description="ISO 8601 timestamp of this report")


# --- Endpoint ---------------------------------------------------------------

@router.get("/axis_score_distribution/", response_model=OverallDistribution)
def get_axis_distribution(
    db: Session = Depends(get_session),
    axis_name: Optional[str] = Query(None, description="Filter to a specific axis name"),
) -> OverallDistribution:
    """Return distribution statistics for LLM axis scores."""
    scored_at = datetime.now(timezone.utc).isoformat()

    # Get distinct axis names
    if axis_name:
        axis_names = [axis_name]
    else:
        axis_result = db.execute(
            select(McpLlmAxisScore.axis_name).distinct()
        ).scalars().all()
        axis_names = list(axis_result)

    by_axis: List[AxisDistribution] = []

    for axis in axis_names:
        # Total scores for this axis
        total = db.execute(
            select(func.count(McpLlmAxisScore.id))
            .where(McpLlmAxisScore.axis_name == axis)
        ).scalar() or 0

        if total == 0:
            continue

        # Bucket distribution
        bucket_rows = (
            db.execute(
                select(
                    McpLlmAxisScore.label,
                    McpLlmAxisScore.label_index,
                    func.count(McpLlmAxisScore.id).label("cnt"),
                )
                .where(McpLlmAxisScore.axis_name == axis)
                .group_by(McpLlmAxisScore.label, McpLlmAxisScore.label_index)
                .order_by(McpLlmAxisScore.label_index)
            )
            .all()
        )

        buckets = [
            AxisBucket(
                label=row.label or "unknown",
                label_index=row.label_index,
                count=row.cnt,
                pct=round((row.cnt / total) * 100, 2),
            )
            for row in bucket_rows
        ]

        # Aggregates
        agg = db.execute(
            select(
                func.count(McpLlmAxisScore.id).filter(McpLlmAxisScore.escalated == True).label("esc_cnt"),
                func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
                func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
                func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
            )
            .where(McpLlmAxisScore.axis_name == axis)
        ).first()

        by_axis.append(AxisDistribution(
            axis_name=axis,
            total_scores=total,
            buckets=buckets,
            escalated_count=agg.esc_cnt or 0,
            avg_p_top=round(agg.avg_p_top, 4) if agg.avg_p_top else None,
            avg_p_critical=round(agg.avg_p_critical, 4) if agg.avg_p_critical else None,
            avg_p_danger=round(agg.avg_p_danger, 4) if agg.avg_p_danger else None,
        ))

    total_servers = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar() or 0

    return OverallDistribution(
        total_servers_scored=total_servers,
        by_axis=by_axis,
        scored_at=scored_at,
    )


# --- Self-test --------------------------------------------------------------

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine)
    _test_session = TestSession()

    def _override():
        return _test_session

    from app.main import app
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Add test data
    _test_session.add(McpServerRegistry(server_id="srv-001", name="test-server", registry_source="test"))
    _test_session.add(McpLlmAxisScore(
        id=1, server_id="srv-001", axis_name="overall_risk", label="critical", label_index=0,
        probs="[0.8,0.15,0.05]", p_top=0.8, p_critical=0.8, p_danger=0.15,
        model_version="v1", adapter_sha256="abc", scored_at=datetime.now(timezone.utc)
    ))
    _test_session.add(McpLlmAxisScore(
        id=2, server_id="srv-001", axis_name="overall_risk", label="danger", label_index=1,
        probs="[0.2,0.6,0.2]", p_top=0.6, p_critical=0.2, p_danger=0.6,
        model_version="v1", adapter_sha256="abc", scored_at=datetime.now(timezone.utc)
    ))
    _test_session.add(McpLlmAxisScore(
        id=3, server_id="srv-001", axis_name="security", label="elevated", label_index=2,
        probs="[0.1,0.3,0.6]", p_top=0.6, p_critical=0.1, p_danger=0.3,
        model_version="v1", adapter_sha256="abc", scored_at=datetime.now(timezone.utc)
    ))
    _test_session.commit()

    resp = client.get("/api/axis_score_distribution/")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert "by_axis" in data
    assert len(data["by_axis"]) == 2
    assert data["total_servers_scored"] == 1
    for axis in data["by_axis"]:
        assert "axis_name" in axis
        assert "buckets" in axis
        for b in axis["buckets"]:
            assert b["count"] > 0
            assert 0 <= b["pct"] <= 100

    # Test axis filter
    resp2 = client.get("/api/axis_score_distribution/?axis_name=security")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["by_axis"]) == 1
    assert data2["by_axis"][0]["axis_name"] == "security"

    print("self-test passed")
