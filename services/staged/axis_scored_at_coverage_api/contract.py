from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


@router.get("/axes/scored-at-coverage")
def get_axis_scored_at_coverage(
    days: int = Query(default=14),
    session=Depends(get_session),
):
    days = max(1, min(90, days))
    now = datetime.now(timezone.utc)
    start_date = now.date() - timedelta(days=days - 1)
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=days)

    scored_date = func.date(McpLlmAxisScore.scored_at)
    rows = session.execute(
        select(
            scored_date.label("date"),
            func.count().label("rows"),
            func.count(McpLlmAxisScore.server_id.distinct()).label(
                "distinct_servers"
            ),
        )
        .where(
            McpLlmAxisScore.scored_at >= start,
            McpLlmAxisScore.scored_at < end,
        )
        .group_by(scored_date)
        .order_by(scored_date)
    ).all()

    total_registered_servers = int(
        session.execute(
            select(func.count()).select_from(McpServerRegistry)
        ).scalar_one()
    )

    series = []
    for row in rows:
        distinct_servers = int(row.distinct_servers)
        coverage_pct = (
            round(distinct_servers * 100.0 / total_registered_servers, 2)
            if total_registered_servers
            else 0.0
        )
        series.append(
            {
                "date": str(row.date),
                "rows": int(row.rows),
                "distinct_servers": distinct_servers,
                "coverage_pct": coverage_pct,
            }
        )

    return {
        "days": days,
        "total_registered_servers": total_registered_servers,
        "series": series,
        "generated_at": now.isoformat(),
    }


def _run_self_test():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    first_date = now - timedelta(days=1)
    second_date = now

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                axis_name TEXT,
                scored_at DATETIME,
                p_top FLOAT
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_server_registry (server_id)
            VALUES ('server-1'), ('server-2'), ('server-3')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores
                (id, server_id, axis_name, scored_at, p_top)
            VALUES
                (1, 'server-1', 'security', ?, 0.91),
                (2, 'server-1', 'quality', ?, 0.82),
                (3, 'server-1', 'security', ?, 0.88),
                (4, 'server-2', 'security', ?, 0.77)
            """,
            (
                first_date,
                first_date,
                second_date,
                second_date,
            ),
        )

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/axes/scored-at-coverage", params={"days": 14}
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["days"] == 14
            assert payload["total_registered_servers"] == 3
            assert len(payload["series"]) == 2
            assert all(
                0 <= item["coverage_pct"] <= 100 for item in payload["series"]
            )
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()