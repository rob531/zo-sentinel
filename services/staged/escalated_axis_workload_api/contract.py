from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")


class EscalatedAxisCount(BaseModel):
    axis_name: str
    escalated_to: Optional[str]
    count: int


class EscalatedRuleVersionCount(BaseModel):
    decision_rule_version: str
    count: int


class RecentEscalation(BaseModel):
    server_id: str
    axis_name: str
    escalated_to: Optional[str]
    scored_at: str


class EscalatedAxesResponse(BaseModel):
    days: int
    total_escalated: int
    escalated_servers: int
    by_axis: list[EscalatedAxisCount]
    by_rule_version: list[EscalatedRuleVersionCount]
    recent: list[RecentEscalation]


def _scored_at_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@router.get(
    "/scoring/escalated-axes",
    response_model=EscalatedAxesResponse,
)
def get_escalated_axes(
    days: int = Query(30, ge=1, le=365),
    axis_name: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> EscalatedAxesResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conditions = [
        McpLlmAxisScore.escalated.is_(True),
        McpLlmAxisScore.scored_at >= cutoff,
    ]
    if axis_name is not None:
        conditions.append(McpLlmAxisScore.axis_name == axis_name)

    total_escalated = int(
        session.execute(
            select(func.count()).select_from(McpLlmAxisScore).where(*conditions)
        ).scalar_one()
    )
    escalated_servers = int(
        session.execute(
            select(func.count(func.distinct(McpLlmAxisScore.server_id)))
            .select_from(McpLlmAxisScore)
            .where(*conditions)
        ).scalar_one()
    )

    axis_rows = session.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            func.count().label("item_count"),
        )
        .where(*conditions)
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
        )
        .order_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
        )
    ).all()

    rule_rows = session.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            func.count().label("item_count"),
        )
        .where(*conditions)
        .group_by(McpLlmAxisScore.decision_rule_version)
        .order_by(McpLlmAxisScore.decision_rule_version)
    ).all()

    recent_rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.scored_at,
        )
        .where(*conditions)
        .order_by(
            desc(McpLlmAxisScore.scored_at),
            desc(McpLlmAxisScore.id),
        )
        .limit(100)
    ).all()

    return EscalatedAxesResponse(
        days=days,
        total_escalated=total_escalated,
        escalated_servers=escalated_servers,
        by_axis=[
            {
                "axis_name": row.axis_name,
                "escalated_to": row.escalated_to,
                "count": int(row.item_count),
            }
            for row in axis_rows
        ],
        by_rule_version=[
            {
                "decision_rule_version": row.decision_rule_version,
                "count": int(row.item_count),
            }
            for row in rule_rows
        ],
        recent=[
            {
                "server_id": row.server_id,
                "axis_name": row.axis_name,
                "escalated_to": row.escalated_to,
                "scored_at": _scored_at_iso(row.scored_at),
            }
            for row in recent_rows
        ],
    )


def run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    escalated INTEGER NOT NULL,
                    escalated_to TEXT,
                    decision_rule_version TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    scored_at DATETIME NOT NULL
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id, server_id, axis_name, escalated, escalated_to,
                    decision_rule_version, model_version, scored_at
                ) VALUES (
                    :id, :server_id, :axis_name, :escalated, :escalated_to,
                    :decision_rule_version, :model_version, :scored_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-001",
                    "axis_name": "availability",
                    "escalated": 1,
                    "escalated_to": "tier-2",
                    "decision_rule_version": "rule-v1",
                    "model_version": "model-v1",
                    "scored_at": now - timedelta(minutes=4),
                },
                {
                    "id": 2,
                    "server_id": "server-002",
                    "axis_name": "security",
                    "escalated": 1,
                    "escalated_to": "tier-3",
                    "decision_rule_version": "rule-v1",
                    "model_version": "model-v1",
                    "scored_at": now - timedelta(minutes=3),
                },
                {
                    "id": 3,
                    "server_id": "server-003",
                    "axis_name": "availability",
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "rule-v1",
                    "model_version": "model-v1",
                    "scored_at": now - timedelta(minutes=2),
                },
                {
                    "id": 4,
                    "server_id": "server-004",
                    "axis_name": "security",
                    "escalated": 0,
                    "escalated_to": None,
                    "decision_rule_version": "rule-v1",
                    "model_version": "model-v1",
                    "scored_at": now - timedelta(minutes=1),
                },
            ],
        )

    test_session_factory = sessionmaker(bind=engine, autoflush=False)

    def override_get_session():
        test_session = test_session_factory()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scoring/escalated-axes")
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["days"] == 30
        assert payload["total_escalated"] == 2
        assert payload["escalated_servers"] == 2
        axis_counts = {
            item["axis_name"]: item["count"] for item in payload["by_axis"]
        }
        assert axis_counts == {"availability": 1, "security": 1}
        assert len(payload["recent"]) == 2

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run_self_test()