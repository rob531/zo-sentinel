from __future__ import annotations

from datetime import date, datetime, timezone
from inspect import isawaitable
from typing import Any, Iterable, Mapping

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import FreshnessStat

APP_DB_MODEL: type[FreshnessStat] = FreshnessStat

DEFAULT_THRESHOLD_SECONDS = 1800
DEFAULT_THRESHOLD = DEFAULT_THRESHOLD_SECONDS
THRESHOLD_OVERRIDES = {
    "write_service": 300,
    "self_diagnostics": 600,
    "rug_pull_monitor": 28800,
    "default": DEFAULT_THRESHOLD_SECONDS,
}
THRESHOLDS = THRESHOLD_OVERRIDES

__all__ = [
    "DEFAULT_THRESHOLD",
    "DEFAULT_THRESHOLD_SECONDS",
    "THRESHOLDS",
    "THRESHOLD_OVERRIDES",
    "compute_health_freshness",
    "get_daemon_freshness",
    "get_daemon_health_freshness",
    "get_health_freshness",
    "get_refresh_sources",
    "get_service_health_freshness",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"Unsupported last_heartbeat value: {value!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_threshold(service: str | None) -> int:
    return THRESHOLD_OVERRIDES.get(service or "", DEFAULT_THRESHOLD_SECONDS)


def get_refresh_sources(services: Iterable[Mapping[str, Any]]) -> list[str]:
    return [str(item["service"]) for item in services if bool(item["stale"])]


def compute_health_freshness(
    rows: Iterable[Mapping[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    services: list[dict[str, Any]] = []

    for row in rows:
        record = dict(row)
        service = str(record["service"])
        heartbeat = record.get("last_heartbeat")
        heartbeat_at = _parse_timestamp(heartbeat)
        age_seconds = (current_time - heartbeat_at).total_seconds()
        threshold = get_threshold(service)
        stale = age_seconds > threshold

        services.append(
            {
                "service": service,
                "status": record.get("status"),
                "last_heartbeat": heartbeat,
                "age_seconds": age_seconds,
                "stale": stale,
            }
        )

    services.sort(key=lambda item: (-float(item["age_seconds"]), item["service"]))

    stale_count = sum(1 for item in services if item["stale"])
    oldest = services[0] if services else None

    return {
        "services": services,
        "summary": {
            "total": len(services),
            "stale_count": stale_count,
            "ok_count": len(services) - stale_count,
            "oldest_service": oldest["service"] if oldest else None,
            "oldest_age_seconds": oldest["age_seconds"] if oldest else 0,
        },
        "refresh_sources": get_refresh_sources(services),
    }


async def get_health_freshness(
    session: AsyncSession = Depends(get_session),
    service_name: str | None = None,
) -> dict[str, Any]:
    if service_name is None:
        statement = text(
            """
            SELECT service, last_heartbeat, status, meta
            FROM service_health
            """
        )
        parameters: dict[str, Any] = {}
    else:
        statement = text(
            """
            SELECT service, last_heartbeat, status, meta
            FROM service_health
            WHERE service = :service_name
            """
        )
        parameters = {"service_name": service_name}

    result = session.execute(statement, parameters)
    if isawaitable(result):
        result = await result

    rows = result.mappings().all()
    return compute_health_freshness(rows)


async def get_daemon_health_freshness(
    session: AsyncSession = Depends(get_session),
    service_name: str | None = None,
) -> dict[str, Any]:
    return await get_health_freshness(session=session, service_name=service_name)


async def get_daemon_freshness(
    session: AsyncSession = Depends(get_session),
    service_name: str | None = None,
) -> dict[str, Any]:
    return await get_health_freshness(session=session, service_name=service_name)


async def get_service_health_freshness(
    session: AsyncSession = Depends(get_session),
    service_name: str | None = None,
) -> dict[str, Any]:
    return await get_health_freshness(session=session, service_name=service_name)


def _self_test() -> None:
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = Session

    with SessionLocal(engine) as session:
        session.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT PRIMARY KEY,
                    last_heartbeat TIMESTAMP,
                    status TEXT,
                    meta TEXT
                )
                """
            )
        )
        now = _utc_now()
        session.execute(
            text(
                """
                INSERT INTO service_health (service, last_heartbeat, status, meta)
                VALUES (:service, :last_heartbeat, :status, :meta)
                """
            ),
            [
                {
                    "service": "fresh_daemon",
                    "last_heartbeat": now.isoformat(),
                    "status": "running",
                    "meta": None,
                },
                {
                    "service": "two_hour_daemon",
                    "last_heartbeat": (now - timedelta(hours=2)).isoformat(),
                    "status": "running",
                    "meta": None,
                },
                {
                    "service": "three_day_daemon",
                    "last_heartbeat": (now - timedelta(days=3)).isoformat(),
                    "status": "running",
                    "meta": None,
                },
            ],
        )
        session.commit()

    def override_get_session():
        with SessionLocal(engine) as session:
            yield session

    that_app = FastAPI()

    @that_app.get("/api/daemons/health-freshness")
    async def health_freshness_endpoint(
        freshness: dict[str, Any] = Depends(get_health_freshness),
    ) -> dict[str, Any]:
        return {
            "generated_at": _utc_now().isoformat(),
            "threshold_overrides": THRESHOLD_OVERRIDES,
            "services": freshness["services"],
            "summary": freshness["summary"],
        }

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/daemons/health-freshness")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["services"]) == 3
        services = {item["service"]: item for item in payload["services"]}
        assert services["fresh_daemon"]["stale"] is False
        assert services["three_day_daemon"]["stale"] is True
        assert payload["summary"]["stale_count"] == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()