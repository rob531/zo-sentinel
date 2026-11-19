"""logic.py -- daemon liveness summary data and computation layer."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_HEALTHY = "healthy"
_STALE = "stale"
_DOWN = "down"


def _parse_heartbeat(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_meta(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if isinstance(decoded, Mapping):
            return dict(decoded)
    return None


def _service_health_table(db: Session) -> Table:
    metadata = MetaData()
    return Table(
        "service_health",
        metadata,
        autoload_with=db.get_bind(),
    )


def get_daemon_liveness_summary(db: Session) -> Dict[str, Any]:
    """Summarise liveness using the real app database service_health table."""
    table = _service_health_table(db)
    rows = db.execute(select(table)).mappings().all()
    now = datetime.now(timezone.utc)

    services = []
    for row in rows:
        heartbeat = _parse_heartbeat(row.get("last_heartbeat"))
        age_seconds = (
            max(0.0, (now - heartbeat).total_seconds())
            if heartbeat is not None
            else None
        )
        stored_status = row.get("status")
        stored_status = (
            stored_status.strip().lower()
            if isinstance(stored_status, str)
            else str(stored_status or "unknown").strip().lower()
        )

        if age_seconds is None:
            status = stored_status or _HEALTHY
        elif age_seconds > 3600:
            status = _DOWN
        elif age_seconds > 300:
            status = _STALE
        else:
            status = _HEALTHY

        service_value = row.get("service")
        services.append(
            {
                "service": (
                    service_value
                    if isinstance(service_value, str)
                    else str(service_value or "")
                ),
                "status": status,
                "last_heartbeat": (
                    heartbeat.isoformat() if heartbeat is not None else None
                ),
                "age_seconds": age_seconds,
                "meta": _normalise_meta(row.get("meta")),
            }
        )

    services.sort(
        key=lambda item: (
            item["age_seconds"] is not None,
            item["age_seconds"] if item["age_seconds"] is not None else 0.0,
        ),
        reverse=True,
    )

    healthy = sum(item["status"] == _HEALTHY for item in services)
    stale = sum(item["status"] == _STALE for item in services)
    down = sum(item["status"] == _DOWN for item in services)

    return {
        "generated_at": now.isoformat(),
        "total": len(services),
        "healthy": healthy,
        "stale": stale,
        "down": down,
        "services": services,
    }


def summarize_daemon_liveness(db: Session) -> Dict[str, Any]:
    return get_daemon_liveness_summary(db)


def daemon_liveness_summary(db: Session) -> Dict[str, Any]:
    return get_daemon_liveness_summary(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine = __import__("sqlalchemy").create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    service_health = Table(
        "service_health",
        metadata,
        Column("service", String, primary_key=True),
        Column("last_heartbeat", DateTime, nullable=True),
        Column("status", String, nullable=False),
        Column("meta", String, nullable=True),
    )
    metadata.create_all(engine)

    session_factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.execute(
            service_health.insert(),
            [
                {
                    "service": "fresh",
                    "last_heartbeat": now,
                    "status": "healthy",
                    "meta": None,
                },
                {
                    "service": "stale",
                    "last_heartbeat": now.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=900),
                    "status": "healthy",
                    "meta": None,
                },
                {
                    "service": "old",
                    "last_heartbeat": now.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=7200),
                    "status": "healthy",
                    "meta": None,
                },
            ],
        )
        session.commit()

    test_app = FastAPI()

    def override_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/ops/daemon-liveness")
    def daemon_liveness_endpoint(
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_daemon_liveness_summary(db)

    with TestClient(test_app) as client:
        response = client.get("/api/ops/daemon-liveness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["healthy"] == 1
        assert payload["stale"] == 1
        assert payload["down"] == 1
        ages = [item["age_seconds"] for item in payload["services"]]
        assert ages == sorted(ages, reverse=True)

    print("PASS")