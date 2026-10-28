from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

STALE_THRESHOLD_SECONDS = 300


def _heartbeat_age_seconds(last_heartbeat: Any, now: datetime) -> float:
    if last_heartbeat is None:
        return float("inf")

    heartbeat = last_heartbeat
    if isinstance(heartbeat, str):
        value = heartbeat.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            heartbeat = datetime.fromisoformat(value)
        except ValueError:
            try:
                heartbeat = datetime.fromtimestamp(float(value), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError):
                return float("inf")
    elif isinstance(heartbeat, (int, float)):
        heartbeat = datetime.fromtimestamp(heartbeat, tz=timezone.utc)
    elif isinstance(heartbeat, date) and not isinstance(heartbeat, datetime):
        heartbeat = datetime.combine(heartbeat, datetime.min.time(), tzinfo=timezone.utc)

    if not isinstance(heartbeat, datetime):
        return float("inf")
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)

    return max(0.0, (now - heartbeat).total_seconds())


def get_status_mix(session: Session = Depends(get_session)) -> dict[str, Any]:
    result = session.execute(
        text(
            """
            SELECT service, status, last_heartbeat, meta
            FROM service_health
            """
        )
    )
    rows = result.mappings().all()
    now = datetime.now(timezone.utc)

    by_status: dict[str, int] = {}
    stale_services: list[dict[str, Any]] = []

    for row in rows:
        status_value = row["status"]
        status = str(status_value) if status_value is not None else "unknown"
        by_status[status] = by_status.get(status, 0) + 1

        age_seconds = _heartbeat_age_seconds(row["last_heartbeat"], now)
        if age_seconds > STALE_THRESHOLD_SECONDS:
            stale_services.append(
                {
                    "service": str(row["service"]),
                    "age_seconds": int(age_seconds),
                    "status": status,
                }
            )

    stale_services.sort(key=lambda item: item["service"])
    healthy_count = sum(
        count
        for status, count in by_status.items()
        if status.lower() in {"ok", "healthy"}
    )

    return {
        "total_services": len(rows),
        "by_status": by_status,
        "stale_services": stale_services,
        "healthy_count": healthy_count,
    }


get_service_health_status_mix = get_status_mix
get_health_status_mix = get_status_mix
get_status_mix_data = get_status_mix


if __name__ == "__main__":
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
                    service TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_heartbeat TIMESTAMP,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO service_health (service, status, last_heartbeat, meta)
                VALUES
                    ('api-a', 'ok', CURRENT_TIMESTAMP, NULL),
                    ('api-b', 'ok', CURRENT_TIMESTAMP, NULL),
                    ('api-c', 'degraded', CURRENT_TIMESTAMP, NULL),
                    ('worker-a', 'ok', '2000-01-01 00:00:00', NULL)
                """
            )
        )

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/service-health/status-mix")
    def status_mix(session: Session = Depends(get_session)):
        return get_status_mix(session)

    response = TestClient(that_app).get("/api/service-health/status-mix")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_services"] == 4
    assert payload["by_status"] == {"ok": 3, "degraded": 1}
    assert payload["healthy_count"] == 3
    assert any(item["service"] == "worker-a" for item in payload["stale_services"])

    print("PASS")