from __future__ import annotations

from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Mapping

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


SERVICE_STALE_THRESHOLDS: dict[str, int] = {
    "api": 300,
    "collector": 300,
    "daemon": 300,
    "scheduler": 300,
    "service": 300,
}
DEFAULT_STALE_THRESHOLD_SECONDS = 3600

_HEALTH_ROLLUP_SQL = text(
    """
    WITH grouped AS (
        SELECT service, MAX(last_heartbeat) AS last_heartbeat
        FROM service_health
        GROUP BY service
    )
    SELECT
        health.service,
        health.status,
        health.last_heartbeat
    FROM service_health AS health
    INNER JOIN grouped
        ON grouped.service = health.service
       AND (
            health.last_heartbeat = grouped.last_heartbeat
            OR (
                health.last_heartbeat IS NULL
                AND grouped.last_heartbeat IS NULL
            )
       )
    """
)


def _threshold_for(service: str, thresholds: Mapping[str, int]) -> int:
    return int(thresholds.get(service, DEFAULT_STALE_THRESHOLD_SECONDS))


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        result = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text_value = str(value).strip()
        if text_value.endswith("Z"):
            text_value = text_value[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text_value)
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _timestamp(value: Any) -> str | None:
    result = _as_datetime(value)
    return result.isoformat().replace("+00:00", "Z") if result is not None else None


async def get_service_health_rollup(
    session: Any = Depends(get_session),
    stale_thresholds: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    thresholds = stale_thresholds or SERVICE_STALE_THRESHOLDS
    result = session.execute(_HEALTH_ROLLUP_SQL)
    if isawaitable(result):
        result = await result

    rows = result.fetchall()
    now = datetime.now(timezone.utc)
    by_service: dict[str, dict[str, Any]] = {}

    for row in rows:
        service_value = row[0]
        service = str(service_value) if service_value is not None else ""
        if service in by_service:
            continue

        heartbeat = _as_datetime(row[2])
        threshold = _threshold_for(service, thresholds)

        if heartbeat is None:
            age_seconds = None
            state = "unknown"
        else:
            age_seconds = max(0, int((now - heartbeat).total_seconds()))
            state = "stale" if age_seconds > threshold else "ok"

        status = row[1]
        if status is not None and not isinstance(status, str):
            status = str(status)

        by_service[service] = {
            "service": service,
            "status": status,
            "last_heartbeat": _timestamp(row[2]),
            "age_seconds": age_seconds,
            "state": state,
            "cycle_threshold_seconds": threshold,
        }

    services = sorted(
        by_service.values(),
        key=lambda item: (item["state"] != "stale", item["service"].lower()),
    )
    totals = {
        "ok": sum(item["state"] == "ok" for item in services),
        "stale": sum(item["state"] == "stale" for item in services),
        "unknown": sum(item["state"] == "unknown" for item in services),
        "total": len(services),
    }

    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "totals": totals,
        "services": services,
    }


get_health_rollup = get_service_health_rollup
get_rollup = get_service_health_rollup


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT NOT NULL,
                    status TEXT,
                    last_heartbeat DATETIME,
                    meta TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO service_health (service, status, last_heartbeat, meta)
                VALUES (:service, :status, :last_heartbeat, :meta)
                """
            ),
            [
                {
                    "service": "fresh-service",
                    "status": "healthy",
                    "last_heartbeat": now,
                    "meta": None,
                },
                {
                    "service": "stale-service",
                    "status": "healthy",
                    "last_heartbeat": now.replace(hour=now.hour - 10)
                    if now.hour >= 10
                    else now - (now - now.replace(hour=0)),
                    "meta": None,
                },
                {
                    "service": "unknown-service",
                    "status": "unknown",
                    "last_heartbeat": None,
                    "meta": None,
                },
            ],
        )

    SessionLocal = Session
    db_session = SessionLocal(engine)

    def override_get_session():
        yield db_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/ops/service-health-rollup")
    async def health_rollup(session=Depends(get_session)):
        return await get_service_health_rollup(session=session)

    with TestClient(test_app) as client:
        response = client.get("/api/ops/service-health-rollup")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["totals"]["total"] == 3
        services = {item["service"]: item for item in payload["services"]}
        assert services["stale-service"]["state"] == "stale"
        assert services["unknown-service"]["state"] == "unknown"

    db_session.close()
    engine.dispose()
    print("PASS")