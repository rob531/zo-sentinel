from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Date, cast, distinct, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


class CoverageEntry(BaseModel):
    date: str
    servers: int
    rows: int
    avg_axes_per_server: float


class CoverageResponse(BaseModel):
    days: int
    series: list[CoverageEntry]


router = APIRouter(prefix="/api")


def _day_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def get_axis_coverage(days: int, session: Session) -> CoverageResponse:
    today = date.today()
    start = today - timedelta(days=days - 1)
    end = today + timedelta(days=1)

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        day_expression = func.date_trunc("day", cast(McpLlmAxisScore.scored_at, Date))
    else:
        day_expression = func.date(McpLlmAxisScore.scored_at)

    per_server = (
        select(
            day_expression.label("day"),
            McpLlmAxisScore.server_id.label("server_id"),
            func.count().label("row_count"),
            func.count(distinct(McpLlmAxisScore.axis_name)).label("axis_count"),
        )
        .where(
            McpLlmAxisScore.scored_at >= start,
            McpLlmAxisScore.scored_at < end,
        )
        .group_by(day_expression, McpLlmAxisScore.server_id)
        .cte("per_server")
    )

    statement = (
        select(
            per_server.c.day.label("day"),
            func.count().label("servers"),
            func.sum(per_server.c.row_count).label("rows"),
            (
                1.0 * func.sum(per_server.c.axis_count) / func.count()
            ).label("avg_axes_per_server"),
        )
        .group_by(per_server.c.day)
        .order_by(per_server.c.day)
    )

    grouped = {
        _day_value(row.day): {
            "servers": int(row.servers),
            "rows": int(row.rows),
            "avg_axes_per_server": float(row.avg_axes_per_server),
        }
        for row in session.execute(statement)
    }

    series: list[CoverageEntry] = []
    for offset in range(days):
        current = (start + timedelta(days=offset)).isoformat()
        values = grouped.get(
            current,
            {"servers": 0, "rows": 0, "avg_axes_per_server": 0.0},
        )
        series.append(CoverageEntry(date=current, **values))

    return CoverageResponse(days=days, series=series)


@router.get("/axis/coverage/days", response_model=CoverageResponse)
def axis_coverage_days(
    days: int = Query(default=30, ge=1, le=366),
    session: Session = Depends(get_session),
) -> CoverageResponse:
    return get_axis_coverage(days, session)


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

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
                    scored_at TIMESTAMP NOT NULL
                )
                """
            )
        )

    today = date.today()
    yesterday = today - timedelta(days=1)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = session_factory()

    test_session.execute(
        text(
            """
            INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, scored_at)
            VALUES
                (:server_id, :axis_name, :scored_at)
            """
        ),
        [
            {
                "server_id": "s1",
                "axis_name": "quality",
                "scored_at": f"{yesterday.isoformat()} 10:00:00",
            },
            {
                "server_id": "s1",
                "axis_name": "quality",
                "scored_at": f"{today.isoformat()} 10:00:00",
            },
            {
                "server_id": "s1",
                "axis_name": "security",
                "scored_at": f"{today.isoformat()} 11:00:00",
            },
            {
                "server_id": "s2",
                "axis_name": "reliability",
                "scored_at": f"{today.isoformat()} 12:00:00",
            },
        ],
    )
    test_session.commit()

    def override_get_session() -> Iterator[Session]:
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/axis/coverage/days",
            params={"days": 2},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 2
    assert len(payload["series"]) == 2

    by_date = {entry["date"]: entry for entry in payload["series"]}
    assert yesterday.isoformat() in by_date
    assert today.isoformat() in by_date

    assert by_date[yesterday.isoformat()]["servers"] == 1
    assert by_date[yesterday.isoformat()]["rows"] == 1
    assert by_date[today.isoformat()]["servers"] == 2
    assert by_date[today.isoformat()]["rows"] == 3
    assert by_date[today.isoformat()]["avg_axes_per_server"] == 1.5

    test_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()