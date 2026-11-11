from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


class EscalationTierCount(BaseModel):
    tier: str
    count: int


class AxisEscalationCount(BaseModel):
    axis_name: str
    escalated: int
    total: int
    rate: float


class TopEscalatedServer(BaseModel):
    server_id: str
    axis_name: str
    escalated_to: Optional[str]
    p_critical: Optional[float]
    scored_at: Optional[datetime]


class AxisEscalationResponse(BaseModel):
    total_rows: int
    escalated_count: int
    escalation_rate: float
    by_escalated_to: list[EscalationTierCount]
    by_axis: list[AxisEscalationCount]
    top_escalated: list[TopEscalatedServer]


def _latest_axis_scores(session: Session) -> list[Any]:
    ranked = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.axis_name.label("axis_name"),
            McpLlmAxisScore.label.label("label"),
            McpLlmAxisScore.label_index.label("label_index"),
            McpLlmAxisScore.p_top.label("p_top"),
            McpLlmAxisScore.p_critical.label("p_critical"),
            McpLlmAxisScore.p_danger.label("p_danger"),
            McpLlmAxisScore.escalated.label("escalated"),
            McpLlmAxisScore.escalated_to.label("escalated_to"),
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
            McpLlmAxisScore.model_version.label("model_version"),
            McpLlmAxisScore.scored_at.label("scored_at"),
            McpLlmAxisScore.id.label("id"),
            func.row_number()
            .over(
                partition_by=(
                    McpLlmAxisScore.server_id,
                    McpLlmAxisScore.axis_name,
                ),
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("row_number"),
        )
    ).subquery()

    statement = (
        select(
            ranked.c.server_id,
            ranked.c.axis_name,
            ranked.c.label,
            ranked.c.label_index,
            ranked.c.p_top,
            ranked.c.p_critical,
            ranked.c.p_danger,
            ranked.c.escalated,
            ranked.c.escalated_to,
            ranked.c.decision_rule_version,
            ranked.c.model_version,
            ranked.c.scored_at,
            ranked.c.id,
        )
        .where(ranked.c.row_number == 1)
        .order_by(
            ranked.c.server_id.asc(),
            ranked.c.axis_name.asc(),
        )
    )

    return list(session.execute(statement).mappings())


def _is_escalated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes", "y"}
    return bool(value)


def _as_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _as_tier(value: Any) -> str:
    if value is None:
        return "none"
    return str(value)


def _as_axis_name(value: Any) -> str:
    if value is None:
        return "none"
    return str(value)


def get_axis_escalations(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = _latest_axis_scores(session)

    tier_counts: dict[str, int] = {}
    axis_counts: dict[str, dict[str, int]] = {}
    escalated_rows: list[dict[str, Any]] = []

    for row in rows:
        server_id = str(row["server_id"])
        axis_name = _as_axis_name(row["axis_name"])
        tier = _as_tier(row["escalated_to"])
        escalated = _is_escalated(row["escalated"])

        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        axis = axis_counts.setdefault(axis_name, {"escalated": 0, "total": 0})
        axis["total"] += 1
        if escalated:
            axis["escalated"] += 1

        if escalated:
            escalated_rows.append(
                {
                    "server_id": server_id,
                    "axis_name": axis_name,
                    "escalated_to": None
                    if row["escalated_to"] is None
                    else str(row["escalated_to"]),
                    "p_critical": _as_optional_float(row["p_critical"]),
                    "scored_at": row["scored_at"],
                }
            )

    escalated_count = sum(
        axis["escalated"] for axis in axis_counts.values()
    )
    total_rows = len(rows)
    escalation_rate = (
        escalated_count / total_rows if total_rows else 0.0
    )

    by_escalated_to = [
        {"tier": tier, "count": tier_counts[tier]}
        for tier in sorted(tier_counts)
    ]

    by_axis = []
    for axis_name in sorted(axis_counts):
        counts = axis_counts[axis_name]
        total = counts["total"]
        by_axis.append(
            {
                "axis_name": axis_name,
                "escalated": counts["escalated"],
                "total": total,
                "rate": counts["escalated"] / total if total else 0.0,
            }
        )

    top_escalated = sorted(
        escalated_rows,
        key=lambda item: (
            item["p_critical"] is None,
            -(item["p_critical"] or 0.0),
            item["server_id"],
            item["axis_name"],
            item["scored_at"] or datetime.min,
        ),
    )

    return {
        "total_rows": total_rows,
        "escalated_count": escalated_count,
        "escalation_rate": escalation_rate,
        "by_escalated_to": by_escalated_to,
        "by_axis": by_axis,
        "top_escalated": top_escalated,
    }


def get_axis_escalation_rollup(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_axis_escalations(session=session)


def get_escalation_rollup(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_axis_escalations(session=session)


if __name__ == "__main__":
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
    McpLlmAxisScore.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    scored_at = datetime(2025, 1, 1, 12, 0, 0)
    score_id = 1

    with TestingSession() as db:
        for index in range(6):
            tier = "tier_a" if index < 3 else "tier_b"
            db.add(
                McpLlmAxisScore(
                    id=score_id,
                    server_id=f"server-{index + 1}",
                    axis_name="security",
                    label="critical",
                    label_index=0,
                    p_top=0.7,
                    p_critical=0.8,
                    p_danger=0.6,
                    escalated=True,
                    escalated_to=tier,
                    decision_rule_version="test-rule",
                    model_version="test-model",
                    scored_at=scored_at,
                )
            )
            score_id += 1

            db.add(
                McpLlmAxisScore(
                    id=score_id,
                    server_id=f"server-{index + 1}",
                    axis_name="quality",
                    label="acceptable",
                    label_index=1,
                    p_top=0.2,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="test-rule",
                    model_version="test-model",
                    scored_at=scored_at,
                )
            )
            score_id += 1

        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/axis/escalations")
    def axis_escalations_endpoint(result=Depends(get_axis_escalations)):
        return result

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/escalations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 12
    assert payload["escalated_count"] == 6
    assert payload["escalation_rate"] == 0.5
    assert {
        item["tier"]: item["count"]
        for item in payload["by_escalated_to"]
    } == {"none": 6, "tier_a": 3, "tier_b": 3}
    assert {
        item["axis_name"]: item
        for item in payload["by_axis"]
    } == {
        "quality": {
            "axis_name": "quality",
            "escalated": 0,
            "total": 6,
            "rate": 0.0,
        },
        "security": {
            "axis_name": "security",
            "escalated": 6,
            "total": 6,
            "rate": 1.0,
        },
    }
    assert len(payload["top_escalated"]) == 6

    print("PASS")