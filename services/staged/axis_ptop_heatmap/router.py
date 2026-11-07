"""HTTP surface for the axis P-top heatmap service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_axis_heatmap
except ImportError:
    try:
        from .logic import axis_heatmap as get_axis_heatmap
    except ImportError:
        from .logic import get_axis_ptop_heatmap as get_axis_heatmap

router = APIRouter(prefix="/api", tags=["axis_ptop_heatmap"])


class HeatmapBucket(BaseModel):
    date: str
    bin: str
    count: int


class AxisHeatmapResponse(BaseModel):
    days: int
    axis: str
    buckets: list[HeatmapBucket]
    axis_means: dict[str, float]


def _normalize_result(result: dict[str, Any], days: int, axis: str) -> AxisHeatmapResponse:
    buckets: list[HeatmapBucket] = []
    for bucket in result.get("buckets", []):
        value = bucket["date"]
        if isinstance(value, datetime):
            value = value.date().isoformat()
        else:
            value = str(value)
        buckets.append(
            HeatmapBucket(
                date=value,
                bin=str(bucket["bin"]),
                count=int(bucket["count"]),
            )
        )

    axis_means = {
        str(name): float(mean)
        for name, mean in result.get("axis_means", {}).items()
    }

    return AxisHeatmapResponse(
        days=int(result.get("days", days)),
        axis=str(result.get("axis", axis)),
        buckets=buckets,
        axis_means=axis_means,
    )


@router.get("/scores/axis-heatmap", response_model=AxisHeatmapResponse)
def get_axis_heatmap_route(
    axis: str = Query(..., description="Axis name"),
    days: int = Query(30, ge=1, le=365, description="Number of days to include"),
    db: Session = Depends(get_session),
) -> AxisHeatmapResponse:
    result = get_axis_heatmap(db, axis=axis, days=days)
    return _normalize_result(result, days=days, axis=axis)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    rows = [
        McpLlmAxisScore(
            id=1,
            server_id="server-1",
            axis_name="overall_risk",
            label="low",
            label_index=1,
            p_top=0.10,
            p_critical=0.01,
            p_danger=0.02,
            escalated=False,
            escalated_to=None,
            scored_at=now - timedelta(days=2),
            model_version="test-model",
            decision_rule_version="test-rule",
        ),
        McpLlmAxisScore(
            id=2,
            server_id="server-2",
            axis_name="overall_risk",
            label="medium",
            label_index=2,
            p_top=0.55,
            p_critical=0.10,
            p_danger=0.20,
            escalated=False,
            escalated_to=None,
            scored_at=now - timedelta(days=2),
            model_version="test-model",
            decision_rule_version="test-rule",
        ),
        McpLlmAxisScore(
            id=3,
            server_id="server-3",
            axis_name="overall_risk",
            label="high",
            label_index=3,
            p_top=0.95,
            p_critical=0.40,
            p_danger=0.50,
            escalated=True,
            escalated_to="security",
            scored_at=now - timedelta(days=1),
            model_version="test-model",
            decision_rule_version="test-rule",
        ),
        McpLlmAxisScore(
            id=4,
            server_id="server-1",
            axis_name="auth_strength",
            label="low",
            label_index=1,
            p_top=0.10,
            p_critical=0.01,
            p_danger=0.02,
            escalated=False,
            escalated_to=None,
            scored_at=now - timedelta(days=2),
            model_version="test-model",
            decision_rule_version="test-rule",
        ),
        McpLlmAxisScore(
            id=5,
            server_id="server-2",
            axis_name="auth_strength",
            label="medium",
            label_index=2,
            p_top=0.55,
            p_critical=0.10,
            p_danger=0.20,
            escalated=False,
            escalated_to=None,
            scored_at=now - timedelta(days=2),
            model_version="test-model",
            decision_rule_version="test-rule",
        ),
        McpLlmAxisScore(
            id=6,
            server_id="server-3",
            axis_name="auth_strength",
            label="high",
            label_index=3,
            p_top=0.95,
            p_critical=0.40,
            p_danger=0.50,
            escalated=True,
            escalated_to="security",
            scored_at=now - timedelta(days=1),
            model_version="test-model",
            decision_rule_version="test-rule",
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get(
        "/api/scores/axis-heatmap",
        params={"axis": "overall_risk", "days": 30},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert sum(bucket["count"] for bucket in payload["buckets"]) == 3
    assert any(
        bucket["bin"] == "40-60" and bucket["count"] >= 1
        for bucket in payload["buckets"]
    )
    assert "overall_risk" in payload["axis_means"]
    print("PASS")