from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401


STALE_AFTER_SECONDS = 300
VERY_STALE_AFTER_SECONDS = 3600


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_heartbeat(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        return _as_utc(value)

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
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
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    parsed = datetime.strptime(candidate, fmt)
                    break
                except ValueError:
                    continue
            else:
                try:
                    return datetime.fromtimestamp(float(candidate), tz=timezone.utc)
                except (OverflowError, OSError, ValueError):
                    return None

        return _as_utc(parsed)

    return None


def _serialize_heartbeat(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _staleness_class(age_seconds: int) -> str:
    if age_seconds >= VERY_STALE_AFTER_SECONDS:
        return "very_stale"
    if age_seconds >= STALE_AFTER_SECONDS:
        return "stale"
    return "ok"


def build_health_matrix(
    rows: Iterable[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = _as_utc(generated_at or datetime.now(timezone.utc))
    latest_by_service: dict[str, tuple[datetime | None, Any, Any]] = {}

    for row in rows:
        service = row.get("service")
        if service is None:
            continue

        service_name = str(service)
        heartbeat = row.get("last_heartbeat")
        parsed = _parse_heartbeat(heartbeat)
        status = row.get("status")

        previous = latest_by_service.get(service_name)
        if previous is None:
            latest_by_service[service_name] = (parsed, status, heartbeat)
            continue

        previous_time = previous[0]
        if parsed is not None and (previous_time is None or parsed >= previous_time):
            latest_by_service[service_name] = (parsed, status, heartbeat)
        elif previous_time is None and parsed is None:
            latest_by_service[service_name] = (None, status, heartbeat)

    services: list[dict[str, Any]] = []
    counts = {"ok": 0, "stale": 0, "very_stale": 0}

    for service_name, (heartbeat_time, status, heartbeat_value) in latest_by_service.items():
        if heartbeat_time is None:
            age_seconds = 0
        else:
            age_seconds = max(0, int((now - heartbeat_time).total_seconds()))

        staleness_class = _staleness_class(age_seconds)
        counts[staleness_class] += 1
        services.append(
            {
                "service": service_name,
                "status": status,
                "last_heartbeat": _serialize_heartbeat(heartbeat_value),
                "age_seconds": age_seconds,
                "staleness_class": staleness_class,
            }
        )

    services.sort(key=lambda item: (-item["age_seconds"], item["service"]))

    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "total": len(services),
        "by_class": counts,
        "services": services,
    }


def fetch_health_matrix(session: Session) -> dict[str, Any]:
    result = session.execute(
        text("SELECT service, last_heartbeat, status FROM service_health")
    )
    return build_health_matrix(result.mappings().all())


def get_daemon_health_matrix(session: Session = Depends(get_session)) -> dict[str, Any]:
    return fetch_health_matrix(session)


def get_health_matrix(session: Session = Depends(get_session)) -> dict[str, Any]:
    return fetch_health_matrix(session)


def get_daemon_health_matrix_data(session: Session) -> dict[str, Any]:
    return fetch_health_matrix(session)


def get_health_matrix_data(session: Session) -> dict[str, Any]:
    return fetch_health_matrix(session)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT PRIMARY KEY,
                    last_heartbeat DATETIME NOT NULL,
                    status TEXT,
                    meta TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                "INSERT INTO service_health "
                "(service, last_heartbeat, status, meta) "
                "VALUES (:service, :last_heartbeat, :status, :meta)"
            ),
            [
                {
                    "service": "fresh-service",
                    "last_heartbeat": (now - timedelta(seconds=30)).isoformat(),
                    "status": "healthy",
                    "meta": "{}",
                },
                {
                    "service": "stale-service",
                    "last_heartbeat": (now - timedelta(minutes=10)).isoformat(),
                    "status": "degraded",
                    "meta": "{}",
                },
                {
                    "service": "very-stale-service",
                    "last_heartbeat": (now - timedelta(hours=2)).isoformat(),
                    "status": "down",
                    "meta": "{}",
                },
            ],
        )

    test_app = FastAPI()

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/daemons/health-matrix")
    def health_matrix(session: Session = Depends(get_session)):
        return fetch_health_matrix(session)

    response = TestClient(test_app).get("/api/daemons/health-matrix")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["by_class"] == {"ok": 1, "stale": 1, "very_stale": 1}
    assert payload["services"][0]["service"] == "very-stale-service"
    print("PASS")