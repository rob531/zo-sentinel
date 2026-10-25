from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


def get_escalated_to_distribution(
    session: Session = Depends(get_session),
    since_days: int = 30,
) -> dict:
    scored_since = datetime.now(timezone.utc) - timedelta(days=since_days)

    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            func.count().label("count"),
        )
        .where(McpLlmAxisScore.escalated.is_(True))
        .where(McpLlmAxisScore.scored_at >= scored_since)
        .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.escalated_to)
    )

    rows = session.execute(statement).all()

    axes: dict[str, dict] = {}
    for axis_name, escalated_to, count in rows:
        axis = axes.setdefault(
            axis_name,
            {
                "axis_name": axis_name,
                "total": 0,
                "by_target": [],
            },
        )
        axis["total"] += int(count)
        axis["by_target"].append(
            {
                "escalated_to": escalated_to,
                "count": int(count),
            }
        )

    for axis in axes.values():
        axis["by_target"].sort(
            key=lambda target: (
                target["escalated_to"] is None,
                target["escalated_to"] or "",
            )
        )

    return {
        "since_days": since_days,
        "axes": [axes[name] for name in sorted(axes)],
    }


get_axis_escalated_to_distribution = get_escalated_to_distribution
get_escalated_to_distribution_data = get_escalated_to_distribution


if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    axis_name VARCHAR,
                    escalated_to VARCHAR,
                    escalated BOOLEAN,
                    scored_at DATETIME
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (axis_name, escalated_to, escalated, scored_at)
                VALUES
                    (:axis_name, :escalated_to, :escalated, :scored_at)
                """
            ),
            [
                {
                    "axis_name": "axis-a",
                    "escalated_to": "triage",
                    "escalated": True,
                    "scored_at": now,
                },
                {
                    "axis_name": "axis-a",
                    "escalated_to": "triage",
                    "escalated": True,
                    "scored_at": now,
                },
                {
                    "axis_name": "axis-a",
                    "escalated_to": "operations",
                    "escalated": True,
                    "scored_at": now,
                },
                {
                    "axis_name": "axis-b",
                    "escalated_to": "operations",
                    "escalated": True,
                    "scored_at": now,
                },
                {
                    "axis_name": "axis-b",
                    "escalated_to": "operations",
                    "escalated": False,
                    "scored_at": now,
                },
            ],
        )

    TestSession = Session

    def override_get_session():
        session = TestSession(test_engine)
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/axes/escalated-to/distribution")
    def distribution(
        session: Session = Depends(get_session),
        since_days: int = Query(30, ge=1, le=365),
    ):
        return get_escalated_to_distribution(session=session, since_days=since_days)

    response = TestClient(test_app).get(
        "/api/axes/escalated-to/distribution",
        params={"since_days": 30},
    )

    assert response.status_code == 200
    payload = response.json()
    axes = {axis["axis_name"]: axis for axis in payload["axes"]}
    assert axes["axis-a"]["total"] == 3
    assert axes["axis-b"]["total"] == 1
    assert sum(axis["total"] for axis in payload["axes"]) == 4
    assert all(
        target["escalated_to"] != "non-escalated"
        for axis in payload["axes"]
        for target in axis["by_target"]
    )

    print("PASS")