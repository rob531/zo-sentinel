from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

HEARTBEAT_COLUMNS = (
    "heartbeat_at",
    "heartbeat_ts",
    "heartbeat_timestamp",
    "last_heartbeat",
    "last_heartbeat_at",
    "last_seen",
    "last_seen_at",
    "updated_at",
)
STALE_AFTER_SECONDS = 300


def _heartbeat_column(db: Session) -> str:
    bind = db.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("service_health")}
    for name in HEARTBEAT_COLUMNS:
        if name in columns:
            return name
    return "heartbeat_at"


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _meta_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def get_service_health_status_grid(
    db: Session,
    status: str | None = None,
    stale_only: bool = False,
) -> dict[str, Any]:
    heartbeat_column = _heartbeat_column(db)
    preparer = db.get_bind().dialect.identifier_preparer
    quoted_heartbeat = preparer.quote(heartbeat_column)

    sql = (
        "SELECT service, status, meta, "
        f"{quoted_heartbeat} AS heartbeat_value "
        "FROM service_health"
    )
    parameters: dict[str, Any] = {}
    if status is not None:
        sql += " WHERE status = :status"
        parameters["status"] = status

    rows = db.execute(text(sql), parameters).mappings().all()
    now = datetime.now(timezone.utc)
    services: list[dict[str, Any]] = []

    for row in rows:
        heartbeat = _as_datetime(row["heartbeat_value"])
        if heartbeat is None:
            age_seconds = -1
            liveness = "unknown"
        else:
            age_seconds = max(0, int((now - heartbeat).total_seconds()))
            liveness = "stale" if age_seconds > STALE_AFTER_SECONDS else "ok"

        if stale_only and liveness != "stale":
            continue

        services.append(
            {
                "service": str(row["service"]),
                "status": str(row["status"]),
                "age_seconds": age_seconds,
                "liveness": liveness,
                "meta": _meta_dict(row["meta"]),
            }
        )

    services.sort(key=lambda item: item["service"])
    return {
        "total": len(services),
        "stale_count": sum(item["liveness"] == "stale" for item in services),
        "services": services,
    }


get_service_health_grid = get_service_health_status_grid


if __name__ == "__main__":
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service VARCHAR(255) PRIMARY KEY,
                    status VARCHAR(64) NOT NULL,
                    meta JSON,
                    heartbeat_at DATETIME NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO service_health (service, status, meta, heartbeat_at)
                VALUES (:service, :status, :meta, :heartbeat_at)
                """
            ),
            [
                {
                    "service": "service_a",
                    "status": "healthy",
                    "meta": json.dumps({"region": "local"}),
                    "heartbeat_at": datetime.now(timezone.utc) - timedelta(seconds=30),
                },
                {
                    "service": "service_b",
                    "status": "healthy",
                    "meta": None,
                    "heartbeat_at": datetime.now(timezone.utc) - timedelta(minutes=10),
                },
                {
                    "service": "service_c",
                    "status": "degraded",
                    "meta": None,
                    "heartbeat_at": None,
                },
            ],
        )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/ops/service-health")
    def service_health_endpoint(
        db: Session = Depends(get_session),
        status: str | None = None,
        stale_only: bool = False,
    ):
        return get_service_health_status_grid(db, status=status, stale_only=stale_only)

    from fastapi.testclient import TestClient

    with TestClient(that_app) as client:
        response = client.get("/api/ops/service-health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert [item["liveness"] for item in payload["services"]] == [
            "ok",
            "stale",
            "unknown",
        ]
        assert payload["stale_count"] == 1
        assert payload["services"][0]["meta"] == {"region": "local"}
        assert payload["services"][2]["age_seconds"] == -1

    print("PASS")