from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

_MODEL_COLUMN_NAMES = {
    column.name
    for model in (McpServerRegistry, McpLlmAxisScore)
    for column in model.__table__.columns
}
_OPTIONAL_RISK_COLUMNS = (
    "tier",
    "risk_tier",
    "score",
    "risk_score",
    "llm_score",
)


def _clamp_days(days: Any) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 30
    return max(1, min(365, value))


def _existing_risk_columns(session: Session) -> list[str]:
    try:
        available = {
            column["name"]
            for column in inspect(session.get_bind()).get_columns("mcp_risk_register")
        }
    except Exception:
        available = set()

    return [
        column
        for column in _OPTIONAL_RISK_COLUMNS
        if column in _MODEL_COLUMN_NAMES and column in available
    ]


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    raise ValueError(f"Unsupported date value: {value!r}")


def get_growth_data(
    db: Session | None = None,
    days: Any = 30,
    session: Session | None = None,
) -> dict[str, Any]:
    active_session = session or db
    if active_session is None:
        raise ValueError("A database session is required")

    day_count = _clamp_days(days)
    cutoff = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=day_count - 1
    )

    selected_columns = ["server_id", "computed_at", *_existing_risk_columns(active_session)]
    select_list = ", ".join(f'"{column}"' for column in selected_columns)

    statement = text(
        f"""
        SELECT
            DATE_TRUNC('day', computed_at) AS day,
            COUNT(*) AS row_count,
            COUNT(DISTINCT server_id) AS distinct_servers
        FROM mcp_risk_register
        WHERE computed_at >= :cutoff
        GROUP BY DATE_TRUNC('day', computed_at)
        ORDER BY DATE_TRUNC('day', computed_at)
        """
    )
    rows = active_session.execute(statement, {"cutoff": cutoff}).mappings().all()

    series: list[dict[str, Any]] = []
    cumulative = 0
    previous_day: date | None = None

    for row in rows:
        current_day = _as_date(row["day"])
        count = int(row["row_count"])
        cumulative += count
        previous_count = 0
        if previous_day is not None:
            previous_count = next(
                (
                    int(item["count"])
                    for item in series
                    if item["date"] == (current_day - timedelta(days=1)).isoformat()
                ),
                0,
            )
        series.append(
            {
                "date": current_day.isoformat(),
                "count": count,
                "delta": count - previous_count,
                "cumulative": cumulative,
            }
        )
        previous_day = current_day

    return {
        "days": day_count,
        "total_rows": cumulative,
        "distinct_servers": sum(
            int(row["distinct_servers"])
            for row in rows
        )
        if False
        else int(
            active_session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT server_id)
                    FROM mcp_risk_register
                    WHERE computed_at >= :cutoff
                    """
                ),
                {"cutoff": cutoff},
            ).scalar_one()
        ),
        "series": series,
    }


def get_growth_summary(
    db: Session | None = None,
    days: Any = 30,
    session: Session | None = None,
) -> dict[str, Any]:
    return get_growth_data(db=db, days=days, session=session)


def get_risk_register_growth(
    db: Session | None = None,
    days: Any = 30,
    session: Session | None = None,
) -> dict[str, Any]:
    return get_growth_data(db=db, days=days, session=session)


def get_risk_register_growth_data(
    db: Session | None = None,
    days: Any = 30,
    session: Session | None = None,
) -> dict[str, Any]:
    return get_growth_data(db=db, days=days, session=session)


if __name__ == "__main__":
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def _register_date_trunc(dbapi_connection: Any, _connection_record: Any) -> None:
        def date_trunc(_unit: str, value: Any) -> str | None:
            if value is None:
                return None
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        dbapi_connection.create_function("date_trunc", 2, date_trunc)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    computed_at DATETIME NOT NULL,
                    risk_tier TEXT,
                    risk_score REAL
                )
                """
            )
        )
        now = datetime.utcnow()
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register
                    (server_id, computed_at, risk_tier, risk_score)
                VALUES
                    (:server_id, :computed_at, :risk_tier, :risk_score)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "computed_at": now - timedelta(days=1),
                    "risk_tier": "medium",
                    "risk_score": 50.0,
                },
                {
                    "server_id": "server-b",
                    "computed_at": now - timedelta(hours=1),
                    "risk_tier": "high",
                    "risk_score": 80.0,
                },
                {
                    "server_id": "server-a",
                    "computed_at": now - timedelta(hours=2),
                    "risk_tier": "high",
                    "risk_score": 90.0,
                },
            ],
        )

    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/risk-register/growth")
    def risk_register_growth(
        days: int = Query(30),
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_growth_data(db=db, days=days)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk-register/growth", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["series"]) == 2
        assert max(item["count"] for item in payload["series"]) == 2
        assert payload["distinct_servers"] == 2
        assert payload["total_rows"] == 3

    print("PASS")