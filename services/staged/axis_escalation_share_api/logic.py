from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()

KNOWN_AXIS_COUNT = 7


def _normalise_axis_name(axis_name: Optional[str]) -> Optional[str]:
    if axis_name is None:
        return None
    normalised = axis_name.strip()
    return normalised or None


def _escalated_target(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _share(escalated: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(escalated / total, 6)


@router.get("/api/scoring/axis/escalation-share")
def get_axis_escalation_share(
    axis_name: Optional[str] = Query(default=None),
    days: int = Query(default=30),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    requested_axis = _normalise_axis_name(axis_name)
    window_days = max(1, min(int(days), 365))

    if requested_axis is not None:
        known_axes = session.execute(
            select(distinct(McpLlmAxisScore.axis_name))
        ).scalars().all()
        known_axis_names = {value for value in known_axes if value is not None}
        if requested_axis not in known_axis_names:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown axis_name: {requested_axis}",
            )

    cutoff = datetime.utcnow() - timedelta(days=window_days)
    statement = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
            func.count(McpLlmAxisScore.id),
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
        )
    )
    if requested_axis is not None:
        statement = statement.where(
            McpLlmAxisScore.axis_name == requested_axis
        )

    rows = session.execute(statement).all()

    by_axis_counts: Dict[str, Dict[str, int]] = {}
    escalated_to_counts: Dict[str, int] = {}
    total_scores = 0
    escalated = 0

    for row in rows:
        row_axis = row.axis_name
        row_total = int(row[3])
        row_escalated = bool(row.escalated)

        total_scores += row_total
        if row_escalated:
            escalated += row_total

        axis_counts = by_axis_counts.setdefault(
            row_axis,
            {"total_scores": 0, "escalated": 0},
        )
        axis_counts["total_scores"] += row_total
        if row_escalated:
            axis_counts["escalated"] += row_total
            target = _escalated_target(row.escalated_to)
            if target is not None:
                escalated_to_counts[target] = (
                    escalated_to_counts.get(target, 0) + row_total
                )

    by_axis: List[Dict[str, Any]] = []
    for row_axis in sorted(by_axis_counts):
        counts = by_axis_counts[row_axis]
        by_axis.append(
            {
                "axis_name": row_axis,
                "total_scores": counts["total_scores"],
                "escalated": counts["escalated"],
                "escalated_share": _share(
                    counts["escalated"],
                    counts["total_scores"],
                ),
            }
        )

    return {
        "axis_name": requested_axis or "all",
        "days": window_days,
        "total_scores": total_scores,
        "escalated": escalated,
        "escalated_share": _share(escalated, total_scores),
        "escalated_to": escalated_to_counts,
        "by_axis": by_axis,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR,
                axis_name VARCHAR NOT NULL,
                escalated BOOLEAN NOT NULL,
                escalated_to VARCHAR,
                scored_at DATETIME NOT NULL
            )
            """
        )
        now = datetime.utcnow()
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, escalated, escalated_to, scored_at)
            VALUES
                ('server-1', 'security', 1, 'critical', ?),
                ('server-2', 'privacy', 1, 'danger', ?),
                ('server-3', 'performance', 0, NULL, ?),
                ('server-4', 'cost', 0, NULL, ?),
                ('server-5', 'security', 1, 'critical', ?)
            """,
            (
                now - timedelta(days=2),
                now - timedelta(days=3),
                now - timedelta(days=2),
                now - timedelta(days=3),
                now - timedelta(days=40),
            ),
        )

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/scoring/axis/escalation-share",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_scores"] == 4
        assert payload["escalated"] == 2
        assert payload["escalated_share"] == 0.5
        assert payload["escalated_to"] == {"critical": 1, "danger": 1}
        assert {
            item["axis_name"] for item in payload["by_axis"]
        } == {"security", "privacy", "performance", "cost"}

        filtered = client.get(
            "/api/scoring/axis/escalation-share",
            params={"days": 1},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total_scores"] == 0

        selected = client.get(
            "/api/scoring/axis/escalation-share",
            params={"axis_name": "security", "days": 30},
        )
        assert selected.status_code == 200
        assert selected.json()["total_scores"] == 1
        assert selected.json()["escalated"] == 1
        assert selected.json()["escalated_share"] == 1.0

    test_engine.dispose()
    print("PASS")