from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .router import router


_TIER_SCORE_COLUMNS = tuple(
    sorted(
        {
            "confidence_score",
            "overall_score",
            "risk_score",
            "risk_tier",
            "score",
            "tier",
            "trust_score",
            "trust_tier",
            *(
                column.name
                for column in McpServerRegistry.__table__.columns
                if "tier" in column.name or "score" in column.name
            ),
        }
    )
)


def _sqlite_date_trunc(_unit: str, value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
        sep=" "
    )


def _create_test_schema(engine: Any) -> None:
    tier_score_ddl = ", ".join(f'"{column}" TEXT' for column in _TIER_SCORE_COLUMNS)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE mcp_risk_register (
                    server_id TEXT NOT NULL,
                    computed_at DATETIME NOT NULL,
                    {tier_score_ddl}
                )
                """
            )
        )


def _seed_test_data(session: Session) -> None:
    today = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    rows = [
        ("server-alpha", yesterday, "high", 80.0),
        ("server-alpha", yesterday, "high", 80.0),
        ("server-beta", today, "medium", 50.0),
    ]
    for server_id, computed_at, tier, score in rows:
        session.execute(
            text(
                """
                INSERT INTO mcp_risk_register
                    (server_id, computed_at, risk_tier, risk_score)
                VALUES
                    (:server_id, :computed_at, :risk_tier, :risk_score)
                """
            ),
            {
                "server_id": server_id,
                "computed_at": computed_at,
                "risk_tier": tier,
                "risk_score": score,
            },
        )
    session.commit()


def _router_prefix() -> str:
    if router.prefix.startswith("/api"):
        return ""
    if any(getattr(route, "path", "").startswith("/api/") for route in router.routes):
        return ""
    return "/api"


def run() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _register_date_trunc(dbapi_connection: Any, _connection_record: Any) -> None:
        dbapi_connection.create_function(
            "date_trunc",
            2,
            _sqlite_date_trunc,
            deterministic=True,
        )

    _create_test_schema(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        _seed_test_data(session)

    def override_get_session():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix=_router_prefix())
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/risk-register/growth",
            params={"days": 30},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    series = payload["series"]

    assert len(series) == 2, series
    assert max(int(point["count"]) for point in series) == 2, series
    assert int(payload["distinct_servers"]) == 2, payload
    assert int(payload["total_rows"]) == 3, payload
    assert int(payload["days"]) == 30, payload

    print("PASS")


if __name__ == "__main__":
    run()