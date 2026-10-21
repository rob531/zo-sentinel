from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_DEFAULT_THRESHOLD = 300
_HEALTHY_STATUSES = {"healthy", "running", "ok"}


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, bytes):
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
            try:
                return datetime.fromtimestamp(float(candidate), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return None


def _as_meta(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _threshold(meta: dict[str, Any], max_age: int | None) -> float:
    if max_age is not None:
        return float(max_age)

    value = meta.get("threshold", _DEFAULT_THRESHOLD)
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        threshold = float(_DEFAULT_THRESHOLD)
    return threshold


def get_health_overview(
    session: Session = Depends(get_session),
    max_age: int | None = None,
) -> dict[str, Any]:
    if max_age is not None and max_age < 0:
        raise ValueError("max_age must be non-negative")

    result = session.execute(
        text(
            "SELECT service, last_heartbeat, status, meta "
            "FROM service_health"
        )
    )

    now = datetime.now(timezone.utc)
    services: list[dict[str, Any]] = []

    for row in result:
        service = row[0]
        last_heartbeat = row[1]
        status = row[2]
        meta = _as_meta(row[3])

        heartbeat = _as_utc(last_heartbeat)
        if heartbeat is None:
            age_seconds = 0.0
        else:
            age_seconds = max(0.0, (now - heartbeat).total_seconds())

        threshold = _threshold(meta, max_age)
        services.append(
            {
                "service": service,
                "status": status,
                "last_heartbeat": last_heartbeat,
                "age_seconds": age_seconds,
                "meta": meta,
                "_stale": age_seconds > threshold,
                "_down": status not in _HEALTHY_STATUSES,
            }
        )

    services.sort(key=lambda item: item["age_seconds"], reverse=True)

    stale = sum(1 for item in services if item["_stale"])
    down = sum(1 for item in services if item["_down"])
    healthy = sum(
        1
        for item in services
        if not item["_stale"] and not item["_down"]
    )

    for item in services:
        item.pop("_stale", None)
        item.pop("_down", None)

    return {
        "count": len(services),
        "healthy": healthy,
        "stale": stale,
        "down": down,
        "services": services,
    }


get_service_health_overview = get_health_overview
get_overview = get_health_overview


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(
        bind=engine,
    )
    test_session = test_session_factory()

    test_session.execute(
        text(
            "CREATE TABLE service_health ("
            "service TEXT, "
            "last_heartbeat DATETIME, "
            "status TEXT, "
            "meta TEXT"
            ")"
        )
    )

    current = datetime.now(timezone.utc)
    test_session.execute(
        text(
            "INSERT INTO service_health "
            "(service, last_heartbeat, status, meta) "
            "VALUES (:service, :last_heartbeat, :status, :meta)"
        ),
        [
            {
                "service": "healthy",
                "last_heartbeat": current,
                "status": "healthy",
                "meta": json.dumps({}),
            },
            {
                "service": "stale",
                "last_heartbeat": current.replace(microsecond=0)
                - __import__("datetime").timedelta(seconds=10),
                "status": "healthy",
                "meta": json.dumps({"threshold": 1}),
            },
            {
                "service": "down",
                "last_heartbeat": current,
                "status": "down",
                "meta": json.dumps({}),
            },
            {
                "service": "running",
                "last_heartbeat": current,
                "status": "running",
                "meta": json.dumps({}),
            },
        ],
    )
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/health/overview")
    def health_overview(
        session: Session = Depends(get_session),
        max_age: int | None = None,
    ):
        return get_health_overview(session, max_age)

    with TestClient(test_app) as client:
        response = client.get("/api/health/overview")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 4
        assert payload["stale"] >= 1
        assert payload["down"] >= 1
        assert all(
            "service" in item and "age_seconds" in item
            for item in payload["services"]
        )

    print("PASS")