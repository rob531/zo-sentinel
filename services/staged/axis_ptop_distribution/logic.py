"""logic.py -- axis probability distribution data and computation layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AxisDistributionItem(BaseModel):
    axis_name: str
    count: int
    mean_p_top: Optional[float]
    min_p_top: Optional[float]
    max_p_top: Optional[float]
    escalated_count: int
    distinct_labels: int


class AxisDistributionResponse(BaseModel):
    axes: List[AxisDistributionItem]
    generated_at: str


def axis_distribution(db: Session) -> Dict[str, object]:
    """Compute the per-axis p_top distribution from mcp_llm_axis_scores."""
    escalated_count = func.sum(
        case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
    )

    rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id),
            func.avg(McpLlmAxisScore.p_top),
            func.min(McpLlmAxisScore.p_top),
            func.max(McpLlmAxisScore.p_top),
            escalated_count,
            func.count(func.distinct(McpLlmAxisScore.label)),
        )
        .where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .group_by(McpLlmAxisScore.axis_name)
    ).all()

    axes = [
        AxisDistributionItem(
            axis_name=str(axis_name),
            count=int(count),
            mean_p_top=float(mean_p_top) if mean_p_top is not None else None,
            min_p_top=float(min_p_top) if min_p_top is not None else None,
            max_p_top=float(max_p_top) if max_p_top is not None else None,
            escalated_count=int(escalated_count or 0),
            distinct_labels=int(distinct_labels or 0),
        )
        for axis_name, count, mean_p_top, min_p_top, max_p_top, escalated_count, distinct_labels in rows
    ]

    return {
        "axes": axes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_axis_distribution(db: Session = Depends(get_session)) -> Dict[str, object]:
    """Return the current axis probability distribution."""
    return axis_distribution(db)


router = APIRouter()


@router.get("/axes/distribution", response_model=AxisDistributionResponse)
def axes_distribution(db: Session = Depends(get_session)) -> Dict[str, object]:
    return axis_distribution(db)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as db:
        db.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-1",
                    axis_name="overall_risk",
                    label="high",
                    label_index=2,
                    probs="[0.1, 0.2, 0.7]",
                    p_top=0.7,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=True,
                    escalated_to="security",
                    decision_rule_version="test-rule",
                    model_version="test-model",
                    adapter_sha256="test-adapter",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-2",
                    axis_name="overall_risk",
                    label="medium",
                    label_index=1,
                    probs="[0.3, 0.4, 0.3]",
                    p_top=0.3,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=False,
                    decision_rule_version="test-rule",
                    model_version="test-model",
                    adapter_sha256="test-adapter",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-3",
                    axis_name="auth_strength",
                    label="low",
                    label_index=0,
                    probs="[0.8, 0.1, 0.1]",
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=False,
                    decision_rule_version="test-rule",
                    model_version="test-model",
                    adapter_sha256="test-adapter",
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="server-4",
                    axis_name="capability_breadth",
                    label="medium",
                    label_index=1,
                    probs="[0.2, 0.5, 0.3]",
                    p_top=0.5,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=False,
                    decision_rule_version="test-rule",
                    model_version="test-model",
                    adapter_sha256="test-adapter",
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestingSession() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router, prefix="/api")

    response = TestClient(test_app).get("/api/axes/distribution")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["axes"]) == 3, payload

    axes = {item["axis_name"]: item for item in payload["axes"]}
    assert axes["overall_risk"]["escalated_count"] >= 1, payload
    assert axes["overall_risk"]["count"] == 2, payload
    assert axes["auth_strength"]["count"] == 1, payload
    assert axes["capability_breadth"]["count"] == 1, payload

    print("PASS")


if __name__ == "__main__":
    _self_test()