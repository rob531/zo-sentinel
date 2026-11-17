from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
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
            return datetime.fromisoformat(candidate)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(candidate), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
    return None


def _normalize_meta(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def get_service_health_roster(
    session: Session,
    max_age_seconds: float = 600,
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")

    result = session.execute(
        text(
            "SELECT service, status, last_heartbeat, meta "
            "FROM service_health "
            "ORDER BY service"
        )
    ).mappings()

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)

    roster: list[dict[str, Any]] = []
    for row in result:
        heartbeat = row.get("last_heartbeat")
        heartbeat_datetime = _coerce_datetime(heartbeat)

        if heartbeat_datetime is None:
            age_seconds: Optional[int] = None
            stale = True
        else:
            if heartbeat_datetime.tzinfo is None:
                heartbeat_datetime = heartbeat_datetime.replace(tzinfo=timezone.utc)
            else:
                heartbeat_datetime = heartbeat_datetime.astimezone(timezone.utc)
            age_seconds = max(0, int((reference - heartbeat_datetime).total_seconds()))
            stale = age_seconds > max_age_seconds

        roster.append(
            {
                "service": row.get("service"),
                "status": row.get("status"),
                "last_heartbeat": heartbeat,
                "age_seconds": age_seconds,
                "stale": stale,
                "meta": _normalize_meta(row.get("meta")),
            }
        )

    return roster


def summarize_service_health(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "ok": sum(1 for row in rows if row.get("status") == "ok"),
        "stale": sum(1 for row in rows if row.get("status") == "stale"),
        "other": sum(
            1 for row in rows if row.get("status") not in {"ok", "stale"}
        ),
    }


get_service_health = get_service_health_roster
list_service_health = get_service_health_roster


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE service_health ("
                "service TEXT PRIMARY KEY, "
                "status TEXT NOT NULL, "
                "last_heartbeat TIMESTAMP, "
                "meta TEXT"
                ")"
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                "INSERT INTO service_health "
                "(service, status, last_heartbeat, meta) "
                "VALUES (:service, :status, :last_heartbeat, :meta)"
            ),
            [
                {
                    "service": "daemon-a",
                    "status": "ok",
                    "last_heartbeat": now - timedelta(seconds=10),
                    "meta": json.dumps({"region": "local"}),
                },
                {
                    "service": "daemon-b",
                    "status": "ok",
                    "last_heartbeat": now - timedelta(seconds=3600),
                    "meta": json.dumps({"region": "remote"}),
                },
                {
                    "service": "daemon-c",
                    "status": "error",
                    "last_heartbeat": now - timedelta(seconds=30),
                    "meta": json.dumps({"error": "probe failed"}),
                },
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/ops/service-health")
    def service_health_endpoint(
        max_age_seconds: int = Query(default=600, ge=0),
        session: Session = Depends(get_session),
    ):
        rows = get_service_health_roster(session, max_age_seconds)
        counts = summarize_service_health(rows)
        return {"services": rows, "counts": counts, "total": len(rows)}

    response = TestClient(test_app).get(
        "/api/ops/service-health",
        params={"max_age_seconds": 600},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total"] == 3
    assert payload["counts"]["ok"] == 2
    by_service = {row["service"]: row for row in payload["services"]}
    assert by_service["daemon-a"]["stale"] is False
    assert by_service["daemon-b"]["stale"] is True
    print("PASS")