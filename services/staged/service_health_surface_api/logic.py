from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, Query
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401

DEFAULT_SHORT_CYCLE_THRESHOLD_SECONDS = 300
DEFAULT_STANDARD_THRESHOLD_SECONDS = 14_400

SERVICE_HEALTH_SQL = text(
    """
    SELECT service, last_heartbeat, status, meta
    FROM service_health
    ORDER BY service
    """
)


async def get_service_health(
    session: Any = Depends(get_session),
    threshold_seconds: int | None = Query(None, ge=0),
) -> list[dict[str, Any]]:
    result = session.execute(SERVICE_HEALTH_SQL)
    if hasattr(result, "__await__"):
        result = await result

    services: list[dict[str, Any]] = []
    for row in result.mappings().all():
        metadata = _parse_meta(row["meta"])
        age_seconds = _heartbeat_age_seconds(row["last_heartbeat"])
        threshold = _threshold_for(metadata, threshold_seconds)

        services.append(
            {
                "service": row["service"],
                "status": row["status"],
                "last_heartbeat": row["last_heartbeat"],
                "age_seconds": age_seconds,
                "stale": age_seconds is None or age_seconds > threshold,
            }
        )

    return services


get_service_health_rows = get_service_health


def _parse_meta(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _is_short_cycle(metadata: dict[str, Any]) -> bool:
    if metadata.get("short_cycle") is True:
        return True

    for key in ("cycle", "cadence", "heartbeat_cycle"):
        value = metadata.get(key)
        if isinstance(value, str) and "short" in value.lower():
            return True

    for key in (
        "cycle_seconds",
        "heartbeat_interval_seconds",
        "interval_seconds",
        "poll_interval_seconds",
    ):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value <= DEFAULT_SHORT_CYCLE_THRESHOLD_SECONDS

    return False


def _threshold_for(
    metadata: dict[str, Any], threshold_seconds: int | None
) -> int:
    if threshold_seconds is not None:
        return threshold_seconds
    if _is_short_cycle(metadata):
        return DEFAULT_SHORT_CYCLE_THRESHOLD_SECONDS
    return DEFAULT_STANDARD_THRESHOLD_SECONDS


def _heartbeat_age_seconds(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        heartbeat = value
    elif isinstance(value, date):
        heartbeat = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            heartbeat = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            heartbeat = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)

    age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    return max(0, int(age))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service VARCHAR PRIMARY KEY,
                    last_heartbeat DATETIME NOT NULL,
                    status VARCHAR NOT NULL,
                    meta JSON
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO service_health (service, last_heartbeat, status, meta)
                VALUES (:service, :last_heartbeat, :status, :meta)
                """
            ),
            [
                {
                    "service": "api-daemon",
                    "last_heartbeat": now.replace(tzinfo=None),
                    "status": "healthy",
                    "meta": json.dumps({"cycle": "short"}),
                },
                {
                    "service": "auth-daemon",
                    "last_heartbeat": (
                        now.replace(tzinfo=None) - __import__("datetime").timedelta(seconds=600)
                    ),
                    "status": "degraded",
                    "meta": json.dumps({"short_cycle": True}),
                },
                {
                    "service": "batch-worker",
                    "last_heartbeat": (
                        now.replace(tzinfo=None) - __import__("datetime").timedelta(seconds=600)
                    ),
                    "status": "healthy",
                    "meta": json.dumps({"cycle": "long"}),
                },
                {
                    "service": "reporting-worker",
                    "last_heartbeat": (
                        now.replace(tzinfo=None) - __import__("datetime").timedelta(seconds=600)
                    ),
                    "status": "healthy",
                    "meta": json.dumps({"cycle": "standard"}),
                },
            ],
        )

    def override_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/ops/service-health")
    async def service_health_endpoint(
        threshold_seconds: int | None = Query(None, ge=0),
        session: Any = Depends(get_session),
    ):
        services = await get_service_health(
            session=session, threshold_seconds=threshold_seconds
        )
        return {
            "services": services,
            "stale_count": sum(1 for service in services if service["stale"]),
            "total": len(services),
        }

    with TestClient(that_app) as client:
        response = client.get("/api/ops/service-health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["stale_count"] == 1
        assert payload["total"] == 4
        stale_services = [
            service["service"] for service in payload["services"] if service["stale"]
        ]
        assert stale_services == ["auth-daemon"]

    print("PASS")