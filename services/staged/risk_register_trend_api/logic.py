from datetime import date, timedelta
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

__all__ = ["get_risk_register_trend"]


def get_risk_register_trend(
    session: Session = Depends(get_session),
    days: int = 30,
) -> Dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    today = date.today()
    start_date = today - timedelta(days=days - 1)
    end_date = today + timedelta(days=1)

    daily_sql = text(
        """
        SELECT
            SUBSTR(CAST(computed_at AS VARCHAR), 1, 10) AS bucket_date,
            COUNT(*) AS row_count,
            COUNT(DISTINCT server_id) AS distinct_servers,
            AVG(score) AS mean_score
        FROM mcp_risk_register
        WHERE computed_at >= :start_date
          AND computed_at < :end_date
        GROUP BY 1
        ORDER BY bucket_date
        """
    )
    totals_sql = text(
        """
        SELECT COUNT(DISTINCT server_id) AS distinct_servers
        FROM mcp_risk_register
        WHERE computed_at >= :start_date
          AND computed_at < :end_date
        """
    )
    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }

    daily_rows = session.execute(daily_sql, params).mappings().all()
    series: List[Dict[str, Any]] = [
        {
            "date": str(row["bucket_date"]),
            "row_count": int(row["row_count"]),
            "distinct_servers": int(row["distinct_servers"]),
            "mean_score": float(row["mean_score"] or 0.0),
        }
        for row in daily_rows
    ]

    total_server_row = session.execute(totals_sql, params).mappings().one()
    total_rows = sum(item["row_count"] for item in series)
    total_servers = int(total_server_row["distinct_servers"] or 0)

    return {
        "days": days,
        "series": series,
        "totals": {
            "rows": total_rows,
            "servers": total_servers,
        },
    }


def _self_test() -> None:
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    computed_at DATETIME NOT NULL,
                    server_id TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    score REAL NOT NULL
                )
                """
            )
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register
                    (computed_at, server_id, tier, score)
                VALUES
                    (:computed_at, :server_id, :tier, :score)
                """
            ),
            [
                {
                    "computed_at": now - timedelta(days=1),
                    "server_id": "server-a",
                    "tier": "high",
                    "score": 50.0,
                },
                {
                    "computed_at": now - timedelta(days=1),
                    "server_id": "server-b",
                    "tier": "medium",
                    "score": 80.0,
                },
                {
                    "computed_at": now,
                    "server_id": "server-a",
                    "tier": "high",
                    "score": 70.0,
                },
                {
                    "computed_at": now,
                    "server_id": "server-b",
                    "tier": "medium",
                    "score": 90.0,
                },
            ],
        )

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/risk-register/trend")
    def risk_register_trend(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_risk_register_trend(session=session, days=days)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk-register/trend", params={"days": 2})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["series"]) == 2
        assert any(item["row_count"] == 2 for item in payload["series"])
        assert payload["totals"]["rows"] == 4
        assert payload["totals"]["servers"] == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()