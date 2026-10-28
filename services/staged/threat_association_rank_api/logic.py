from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import Table, MetaData, func, inspect, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_INDICATOR_COLUMNS = (
    "indicator",
    "indicator_value",
    "indicator_id",
    "indicator_hash",
    "threat_indicator",
    "ioc",
    "ioc_value",
    "indicator_type",
)
_TIMESTAMP_COLUMNS = (
    "associated_at",
    "association_at",
    "association_timestamp",
    "last_seen",
    "observed_at",
    "seen_at",
    "created_at",
    "updated_at",
    "timestamp",
)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _resolve_column(columns: set[str], candidates: tuple[str, ...], label: str) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    raise ValueError(f"mcp_threat_associations has no supported {label} column")


def _serialize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_threat_association_rank(
    db: Session = Depends(get_session),
    limit: int = 50,
    days: int = 30,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    db = session or db
    if not isinstance(db, Session):
        raise TypeError("A valid SQLAlchemy Session is required")

    limit = _clamp(limit, 1, 200)
    days = _clamp(days, 1, 365)

    bind = db.get_bind()
    association_metadata = MetaData()
    associations = Table(
        "mcp_threat_associations",
        association_metadata,
        autoload_with=bind,
    )

    available_columns = set(associations.c.keys())
    indicator_column = _resolve_column(
        available_columns,
        _INDICATOR_COLUMNS,
        "indicator",
    )
    timestamp_column = _resolve_column(
        available_columns,
        _TIMESTAMP_COLUMNS,
        "timestamp",
    )

    server_id = associations.c["server_id"]
    indicator = associations.c[indicator_column]
    associated_at = associations.c[timestamp_column]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    statement = (
        select(
            McpServerRegistry.server_id.label("server_id"),
            McpServerRegistry.name.label("name"),
            McpServerRegistry.risk_tier.label("risk_tier"),
            func.count(func.distinct(indicator)).label("indicator_count"),
            func.max(associated_at).label("last_seen"),
        )
        .select_from(
            McpServerRegistry.__table__.join(
                associations,
                McpServerRegistry.server_id == server_id,
            )
        )
        .where(associated_at >= cutoff)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .order_by(
            func.count(func.distinct(indicator)).desc(),
            func.max(associated_at).desc(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(limit)
    )

    rows = db.execute(statement).mappings().all()
    servers = [
        {
            "server_id": str(row["server_id"]),
            "name": str(row["name"] or ""),
            "risk_tier": str(row["risk_tier"] or ""),
            "indicator_count": int(row["indicator_count"]),
            "last_seen": _serialize_timestamp(row["last_seen"]),
        }
        for row in rows
    ]

    return {
        "days": days,
        "count": len(servers),
        "servers": servers,
    }


def get_threat_association_ranking(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return get_threat_association_rank(*args, **kwargs)


def get_threat_association_rankings(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return get_threat_association_rank(*args, **kwargs)


def get_ranked_threat_associations(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return get_threat_association_rank(*args, **kwargs)


def get_threat_association_rank_data(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return get_threat_association_rank(*args, **kwargs)


def rank_threat_associations(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return get_threat_association_rank(*args, **kwargs)


if __name__ == "__main__":
    from fastapi import FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    risk_tier TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_threat_associations (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    indicator TEXT NOT NULL,
                    associated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES
                    ('server-1', 'Alpha Server', 'HIGH_RISK_ISOLATED'),
                    ('server-2', 'Beta Server', 'MEDIUM_RISK'),
                    ('server-3', 'Gamma Server', 'LOW_RISK')
                """
            )
        )

        now = datetime.now(timezone.utc)
        association_rows = [
            ("server-1", "indicator-1", now - timedelta(days=1)),
            ("server-1", "indicator-2", now - timedelta(days=2)),
            ("server-1", "indicator-3", now - timedelta(days=3)),
            ("server-1", "indicator-4", now - timedelta(days=4)),
            ("server-2", "indicator-5", now - timedelta(days=1)),
            ("server-2", "indicator-6", now - timedelta(days=2)),
        ]
        for server_id, indicator, associated_at in association_rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_threat_associations
                        (server_id, indicator, associated_at)
                    VALUES (:server_id, :indicator, :associated_at)
                    """
                ),
                {
                    "server_id": server_id,
                    "indicator": indicator,
                    "associated_at": associated_at,
                },
            )

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat/associations/rank")
    def threat_association_rank_endpoint(
        limit: int = Query(50, ge=1, le=200),
        days: int = Query(30, ge=1, le=365),
        db: Session = Depends(get_session),
    ):
        return get_threat_association_rank(db=db, limit=limit, days=days)

    with TestClient(that_app) as client:
        response = client.get(
            "/api/threat/associations/rank",
            params={"limit": 50, "days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        assert payload["count"] == 2
        assert payload["servers"][0]["indicator_count"] == 4
        assert payload["servers"][0]["risk_tier"] == "HIGH_RISK_ISOLATED"
        assert all(server["server_id"] != "server-3" for server in payload["servers"])

    print("PASS")