from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _database_dialect(session: Session) -> str:
    return session.get_bind().dialect.name


def _register_day_expression(dialect: str) -> str:
    if dialect == "postgresql":
        return "CAST(computed_at AS DATE)"
    return "DATE(computed_at)"


def _normalize_server_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def build_risk_register_trend(session: Session, days: int = 30) -> dict[str, Any]:
    days = max(1, int(days))
    dialect = _database_dialect(session)
    day_expression = _register_day_expression(dialect)
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()

    if dialect == "postgresql":
        cutoff_expression = "CAST(:cutoff AS DATE)"
    else:
        cutoff_expression = ":cutoff"

    series_sql = text(
        f"""
        SELECT
            {day_expression} AS risk_day,
            COUNT(*) AS entries,
            COUNT(DISTINCT server_id) AS distinct_servers
        FROM mcp_risk_register
        WHERE computed_at IS NOT NULL
          AND {day_expression} >= {cutoff_expression}
        GROUP BY {day_expression}
        ORDER BY risk_day ASC
        """
    )

    series_rows = session.execute(
        series_sql,
        {"cutoff": cutoff},
    ).mappings()

    series = [
        {
            "date": row["risk_day"].isoformat()
            if hasattr(row["risk_day"], "isoformat")
            else str(row["risk_day"]),
            "entries": int(row["entries"]),
            "distinct_servers": int(row["distinct_servers"]),
        }
        for row in series_rows
    ]

    registry_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier,
        )
    ).all()
    risk_tiers = {
        _normalize_server_id(server_id): risk_tier
        for server_id, risk_tier in registry_rows
    }

    latest_rows = session.execute(
        text(
            """
            SELECT server_id, computed_at
            FROM mcp_risk_register
            WHERE computed_at IS NOT NULL
            ORDER BY computed_at DESC
            LIMIT :row_limit
            """
        ),
        {"row_limit": 50},
    ).mappings()

    latest = [
        {
            "server_id": _normalize_server_id(row["server_id"]),
            "computed_at": row["computed_at"].isoformat()
            if hasattr(row["computed_at"], "isoformat")
            else str(row["computed_at"]),
            "risk_tier": risk_tiers.get(_normalize_server_id(row["server_id"])),
        }
        for row in latest_rows
    ]

    return {
        "days": days,
        "total_entries": sum(item["entries"] for item in series),
        "series": series,
        "latest": latest,
    }


def get_risk_register_trend(
    session: Session = Depends(get_session),
    days: int = 30,
) -> dict[str, Any]:
    return build_risk_register_trend(session=session, days=days)


get_register_trend = get_risk_register_trend


def _self_test() -> None:
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    testing_session.execute(
        text(
            """
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                risk_tier VARCHAR
            )
            """
        )
    )
    testing_session.execute(
        text(
            """
            CREATE TABLE mcp_risk_register (
                server_id VARCHAR NOT NULL,
                computed_at TIMESTAMP NOT NULL,
                risk_score REAL
            )
            """
        )
    )
    testing_session.execute(
        text(
            """
            INSERT INTO mcp_server_registry (server_id, risk_tier)
            VALUES (:server_id, :risk_tier)
            """
        ),
        [
            {"server_id": "server-a", "risk_tier": "high"},
            {"server_id": "server-b", "risk_tier": "medium"},
        ],
    )

    now = datetime.now(timezone.utc)
    testing_session.execute(
        text(
            """
            INSERT INTO mcp_risk_register (server_id, computed_at, risk_score)
            VALUES (:server_id, :computed_at, :risk_score)
            """
        ),
        [
            {
                "server_id": "server-a",
                "computed_at": now - timedelta(days=2),
                "risk_score": 0.91,
            },
            {
                "server_id": "server-a",
                "computed_at": now - timedelta(days=2),
                "risk_score": 0.93,
            },
            {
                "server_id": "server-b",
                "computed_at": now - timedelta(days=1),
                "risk_score": 0.62,
            },
        ],
    )
    testing_session.commit()

    def override_session():
        with testing_session:
            yield testing_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/risk/register-trend")
    def register_trend(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_risk_register_trend(session=session, days=days)

    with TestClient(that_app) as client:
        response = client.get("/api/risk/register-trend", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert payload["total_entries"] == 3
    assert len(payload["series"]) == 2
    assert payload["series"][0]["entries"] == 2
    assert payload["series"][0]["distinct_servers"] == 1
    assert payload["series"][1]["entries"] == 1
    assert payload["series"][1]["distinct_servers"] == 1
    assert {item["server_id"] for item in payload["latest"]} == {
        "server-a",
        "server-b",
    }
    assert all(len(payload["latest"]) <= 50 for _ in [0])

    testing_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()