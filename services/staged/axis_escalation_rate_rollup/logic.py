"""Logic for the axis escalation-rate rollup service."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class TierAxisEscalation(BaseModel):
    axis_name: str
    scored_count: int
    escalated_count: int
    escalation_rate: float
    latest_scored_at: Optional[str]


class AxisEscalationResponse(BaseModel):
    generated_at: str
    axes: list[TierAxisEscalation]


def _timestamp_to_str(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def axis_escalation_rate_rollup(db: Session) -> AxisEscalationResponse:
    """Compute escalation counts and rates for each scoring axis."""
    escalated_count = func.sum(
        case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
    )

    rows = db.execute(
        select(
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id).label("scored_count"),
            escalated_count.label("escalated_count"),
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .group_by(McpLlmAxisScore.axis_name)
        .order_by(McpLlmAxisScore.axis_name)
    ).all()

    axes = []
    for axis_name, scored_count, escalated_total, latest_scored_at in rows:
        count = int(scored_count or 0)
        escalated = int(escalated_total or 0)
        axes.append(
            TierAxisEscalation(
                axis_name=str(axis_name),
                scored_count=count,
                escalated_count=escalated,
                escalation_rate=escalated / count if count else 0.0,
                latest_scored_at=_timestamp_to_str(latest_scored_at),
            )
        )

    return AxisEscalationResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        axes=axes,
    )


def get_axis_escalation_rate_rollup(db: Session) -> AxisEscalationResponse:
    """Compatibility wrapper for the axis escalation-rate rollup."""
    return axis_escalation_rate_rollup(db)


def get_axis_escalation_rates(db: Session) -> AxisEscalationResponse:
    """Compatibility wrapper returning escalation rates for every axis."""
    return axis_escalation_rate_rollup(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id=101,
                    axis_name="availability",
                    label="elevated",
                    label_index=1,
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.1,
                    escalated=True,
                    escalated_to="tier_2",
                    scored_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    model_version="test-model",
                    adapter_sha256="a" * 64,
                    decision_rule_version="test-rule",
                    probs={},
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id=102,
                    axis_name="integrity",
                    label="normal",
                    label_index=0,
                    p_top=0.9,
                    p_critical=0.05,
                    p_danger=0.05,
                    escalated=False,
                    escalated_to=None,
                    scored_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                    model_version="test-model",
                    adapter_sha256="b" * 64,
                    decision_rule_version="test-rule",
                    probs={},
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id=101,
                    axis_name="safety",
                    label="normal",
                    label_index=0,
                    p_top=0.95,
                    p_critical=0.03,
                    p_danger=0.02,
                    escalated=False,
                    escalated_to=None,
                    scored_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
                    model_version="test-model",
                    adapter_sha256="c" * 64,
                    decision_rule_version="test-rule",
                    probs={},
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get(
        "/api/axis/escalation-rate", response_model=AxisEscalationResponse
    )
    def test_axis_escalation_rate(
        db: Session = Depends(get_session),
    ) -> AxisEscalationResponse:
        return axis_escalation_rate_rollup(db)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/axis/escalation-rate")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["axes"]) == 3
    availability = next(
        axis for axis in payload["axes"] if axis["axis_name"] == "availability"
    )
    assert availability["scored_count"] == 1
    assert availability["escalated_count"] == 1
    assert availability["escalation_rate"] == 1.0
    print("PASS")