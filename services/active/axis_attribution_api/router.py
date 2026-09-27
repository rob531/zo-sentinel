# deps: fastapi, pydantic, sqlalchemy
"""Axis Attribution API.

Surfaces the most decisive axes for a given MCP server by querying
mcp_llm_axis_scores, ordering by |p_top - baseline| descending so the
axes that most strongly drove the overall verdict appear first.

Auth: public.
Data: app tier via get_session + SQLAlchemy ORM on McpLlmAxisScore.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_attribution_api"])


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #

class AxisAttributionItem(BaseModel):
    """Single axis attribution result."""
    axis_name: str = Field(..., description="Name of the axis (e.g. overall_risk)")
    label: Optional[str] = Field(None, description="Axis label (e.g. critical, elevated)")
    p_top: Optional[float] = Field(None, description="Probability of top label")
    p_critical: Optional[float] = Field(None, description="Probability of critical label")
    p_danger: Optional[float] = Field(None, description="Probability of danger label")
    escalated: bool = Field(False, description="Whether this axis was escalated")
    decision_rule_version: Optional[str] = Field(None, description="Decision rule version")
    model_version: Optional[str] = Field(None, description="Scoring model version")
    influence: float = Field(..., description="|p_top - baseline|, higher = more decisive")

    model_config = {"from_attributes": True}


class AxisAttributionResponse(BaseModel):
    """List of axis attributions for a server, ordered by influence."""
    server_id: str = Field(..., description="Server identifier")
    items: List[AxisAttributionItem] = Field(..., description="Axes ordered by influence")
    axis_count: int = Field(..., description="Total number of scored axes")


class AxisSummaryItem(BaseModel):
    """Per-axis summary for aggregate endpoint."""
    axis_name: str = Field(...)
    total_scores: int = Field(..., description="Number of servers scored on this axis")
    avg_p_top: Optional[float] = Field(None)
    avg_p_critical: Optional[float] = Field(None)
    avg_p_danger: Optional[float] = Field(None)
    escalated_count: int = Field(0)
    distinct_labels: List[str] = Field(default_factory=list)


class AxisSummaryResponse(BaseModel):
    """Aggregate axis statistics across all servers."""
    axes: List[AxisSummaryItem] = Field(...)
    scored_at: str = Field(..., description="ISO 8601 timestamp")


class HealthResponse(BaseModel):
    status: str
    service: str = "axis_attribution_api"
    axes_available: int


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

DEFAULT_BASELINE = 0.5


def compute_axis_attribution(
    db: Session,
    server_id: str,
    baseline: float = DEFAULT_BASELINE,
) -> AxisAttributionResponse:
    """Return axis attributions for a server, sorted by influence descending."""
    rows = (
        db.execute(
            select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
        )
        .scalars()
        .all()
    )

    scored = []
    for row in rows:
        p_top = row.p_top if row.p_top is not None else 0.0
        influence = abs(p_top - baseline)
        scored.append((influence, row))

    scored.sort(key=lambda x: x[0], reverse=True)

    items = []
    for influence, row in scored:
        items.append(
            AxisAttributionItem(
                axis_name=row.axis_name,
                label=row.label,
                p_top=row.p_top,
                p_critical=row.p_critical,
                p_danger=row.p_danger,
                escalated=bool(row.escalated) if row.escalated is not None else False,
                decision_rule_version=row.decision_rule_version,
                model_version=row.model_version,
                influence=round(influence, 6),
            )
        )

    return AxisAttributionResponse(
        server_id=server_id,
        items=items,
        axis_count=len(items),
    )


def compute_axis_summary(db: Session) -> AxisSummaryResponse:
    """Return aggregate statistics per axis across all servers."""
    axis_names = (
        db.execute(select(McpLlmAxisScore.axis_name).distinct())
        .scalars()
        .all()
    )

    axes: List[AxisSummaryItem] = []
    for axis_name in axis_names:
        agg_row = db.execute(
            select(
                func.count(McpLlmAxisScore.id).label("cnt"),
                func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
                func.avg(McpLlmAxisScore.p_critical).label("avg_p_critical"),
                func.avg(McpLlmAxisScore.p_danger).label("avg_p_danger"),
                func.count(McpLlmAxisScore.id)
                .filter(McpLlmAxisScore.escalated == True)
                .label("esc_cnt"),
            ).where(McpLlmAxisScore.axis_name == axis_name)
        ).first()

        label_rows = (
            db.execute(
                select(McpLlmAxisScore.label)
                .where(McpLlmAxisScore.axis_name == axis_name)
                .distinct()
            )
            .scalars()
            .all()
        )

        axes.append(
            AxisSummaryItem(
                axis_name=axis_name,
                total_scores=agg_row.cnt or 0,
                avg_p_top=round(agg_row.avg_p_top, 4) if agg_row.avg_p_top else None,
                avg_p_critical=round(agg_row.avg_p_critical, 4) if agg_row.avg_p_critical else None,
                avg_p_danger=round(agg_row.avg_p_danger, 4) if agg_row.avg_p_danger else None,
                escalated_count=agg_row.esc_cnt or 0,
                distinct_labels=[l for l in label_rows if l],
            )
        )

    return AxisSummaryResponse(
        axes=axes,
        scored_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/servers/{server_id}/axis-attribution",
    response_model=AxisAttributionResponse,
    summary="Get axis attribution for a server",
    responses={
        404: {"description": "Server not found"},
    },
)
def get_axis_attribution(
    server_id: str,
    baseline: float = Query(
        DEFAULT_BASELINE,
        ge=0.0,
        le=1.0,
        description="Reference p_top baseline for influence computation",
    ),
    db: Session = Depends(get_session),
) -> AxisAttributionResponse:
    """Return axis attributions for a specific server, ordered by decisiveness."""
    # Verify server exists
    exists = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.server_id == server_id
        )
    ).scalar()
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{server_id}' not found",
        )
    return compute_axis_attribution(db, server_id, baseline)


@router.get(
    "/axis-attribution/summary",
    response_model=AxisSummaryResponse,
    summary="Get axis attribution summary across all servers",
)
def get_axis_summary(
    db: Session = Depends(get_session),
) -> AxisSummaryResponse:
    """Return aggregate axis statistics across all scored servers."""
    return compute_axis_summary(db)


@router.get(
    "/axis-attribution/health",
    response_model=HealthResponse,
    summary="Health check",
)
def health(db: Session = Depends(get_session)) -> HealthResponse:
    """Return service health status and axis availability."""
    count = db.execute(
        select(func.count(McpLlmAxisScore.axis_name)).distinct()
    ).scalar() or 0
    return HealthResponse(status="ok", axes_available=count)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as sess:
        sess.add(McpServerRegistry(
            server_id="srv-test-001",
            name="Test Server",
            registry_source="unit-test",
            url="https://example.com/test",
        ))
        # 7 axes with varying influence
        axis_data = [
            ("overall_risk", "critical",  0.95, True),
            ("auth_strength", "medium",    0.60, False),
            ("capability_breadth", "high", 0.80, False),
            ("data_sensitivity", "low",    0.25, False),
            ("network_egress", "elevated", 0.55, False),
            ("maintainer_trust", "high",   0.75, False),
            ("exploit_surface", "low",     0.20, False),
        ]
        for i, (axis_name, label, p_top, escalated) in enumerate(axis_data):
            sess.add(McpLlmAxisScore(
                id=i + 1,
                server_id="srv-test-001",
                axis_name=axis_name,
                label=label,
                label_index=i,
                p_top=p_top,
                p_critical=max(0.0, p_top - 0.3),
                p_danger=max(0.0, p_top - 0.1),
                escalated=escalated,
                decision_rule_version="v2.0",
                model_version="gpt-4o",
                adapter_sha256="deadbeef",
                scored_at=datetime.now(timezone.utc),
            ))
        sess.commit()

    def _override():
        with TestSession() as s:
            yield s

    # Build local FastAPI app for self-test
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    _that_app = test_app
    _that_app.dependency_overrides[get_session] = _override

    client = TestClient(_that_app)

    # Test 1: get axis attribution
    resp = client.get("/api/servers/srv-test-001/axis-attribution")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["server_id"] == "srv-test-001"
    assert data["axis_count"] == 7, f"Expected 7 axes, got {data['axis_count']}"
    assert len(data["items"]) == 7
    # First item should be most decisive (overall_risk, |0.95-0.5|=0.45)
    assert data["items"][0]["axis_name"] == "overall_risk"
    assert data["items"][0]["label"] == "critical"
    assert data["items"][0]["influence"] > 0, "Influence must be positive"
    # Last should be exploit_surface (|0.20-0.5|=0.30) -- NOT the lowest influence
    # But exploit_surface has |0.20-0.5|=0.30 and data_sensitivity |0.25-0.5|=0.25
    # So exploit_surface should appear after overall_risk and capability_breadth etc.
    axis_names = [item["axis_name"] for item in data["items"]]
    assert "overall_risk" in axis_names
    assert "exploit_surface" in axis_names

    # Test 2: 404 for unknown server
    resp404 = client.get("/api/servers/unknown-server/axis-attribution")
    assert resp404.status_code == 404, f"Expected 404, got {resp404.status_code}"

    # Test 3: axis summary
    resp_sum = client.get("/api/axis-attribution/summary")
    assert resp_sum.status_code == 200, f"Expected 200, got {resp_sum.status_code}: {resp_sum.text}"
    sum_data = resp_sum.json()
    assert "axes" in sum_data
    axis_names_summary = [a["axis_name"] for a in sum_data["axes"]]
    assert "overall_risk" in axis_names_summary, f"overall_risk missing from {axis_names_summary}"
    overall_idx = axis_names_summary.index("overall_risk")
    assert sum_data["axes"][overall_idx]["total_scores"] == 1

    # Test 4: health
    resp_h = client.get("/api/axis-attribution/health")
    assert resp_h.status_code == 200, f"Expected 200, got {resp_h.status_code}"
    h_data = resp_h.json()
    assert h_data["status"] == "ok"
    assert h_data["axes_available"] == 7

    print("PASS")
