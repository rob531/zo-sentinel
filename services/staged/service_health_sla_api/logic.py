"""Data and computation layer for service health SLA reporting."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")
_HEARTBEAT_THRESHOLD_SECONDS = 300


def _health_table(db: Session) -> Table:
    """Reflect the real service_health table from the active database."""
    metadata = MetaData()
    return Table("service_health", metadata, autoload_with=db.get_bind())


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decode_meta(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _age_seconds(last_heartbeat: Any, now: datetime) -> int:
    heartbeat = _parse_timestamp(last_heartbeat)
    if heartbeat is None:
        return 2**31 - 1
    return max(0, int((now - heartbeat).total_seconds()))


def _state_for_age(age_seconds: int, threshold: int) -> str:
    if age_seconds <= threshold:
        return "ok"
    if age_seconds <= 3 * threshold:
        return "stale"
    return "dead"


def get_service_health_sla(
    db: Session,
    service: Optional[str] = None,
    threshold: int = _HEARTBEAT_THRESHOLD_SECONDS,
) -> Dict[str, Any]:
    """Return heartbeat freshness details and state counts for registered daemons."""
    now = datetime.now(timezone.utc)
    table = _health_table(db)
    statement = select(
        table.c.service,
        table.c.last_heartbeat,
        table.c.status,
        table.c.meta,
    ).order_by(table.c.service)
    if service is not None:
        statement = statement.where(table.c.service == service)

    services: list[Dict[str, Any]] = []
    summary = {"ok": 0, "stale": 0, "dead": 0}

    for row in db.execute(statement).all():
        age_seconds = _age_seconds(row.last_heartbeat, now)
        state = _state_for_age(age_seconds, threshold)
        summary[state] += 1
        services.append(
            {
                "service": str(row.service),
                "status": str(row.status) if row.status is not None else "unknown",
                "age_seconds": age_seconds,
                "state": state,
                "meta": _decode_meta(row.meta),
            }
        )

    return {
        "generated_at": now.isoformat(),
        "services": services,
        "summary": summary,
    }


def get_health_sla(
    db: Session,
    service: Optional[str] = None,
    threshold: int = _HEARTBEAT_THRESHOLD_SECONDS,
) -> Dict[str, Any]:
    """Compatibility name for the service health SLA report."""
    return get_service_health_sla(db, service=service, threshold=threshold)


@router.get("/health/sla")
def health_sla(
    service: Optional[str] = None,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Report heartbeat freshness for all daemons or one requested service."""
    return get_service_health_sla(db, service=service)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_metadata = MetaData()
    test_health = Table(
        "service_health",
        test_metadata,
        Column("service", String, primary_key=True),
        Column("last_heartbeat", DateTime(timezone=True)),
        Column("status", String),
        Column("meta", Text),
    )
    test_metadata.create_all(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    current_time = datetime.now(timezone.utc)
    with test_session_factory() as test_db:
        test_db.execute(
            insert(test_health),
            [
                {
                    "service": "fresh-daemon",
                    "last_heartbeat": current_time,
                    "status": "up",
                    "meta": json.dumps({"region": "local"}),
                },
                {
                    "service": "old-daemon",
                    "last_heartbeat": current_time.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=600),
                    "status": "up",
                    "meta": json.dumps({"region": "remote"}),
                },
                {
                    "service": "down-daemon",
                    "last_heartbeat": current_time.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=1200),
                    "status": "down",
                    "meta": json.dumps({"region": "remote"}),
                },
            ],
        )
        test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session_factory()

    with TestClient(test_app) as client:
        response = client.get("/api/health/sla")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["services"]) == 3
        by_service = {item["service"]: item for item in payload["services"]}
        assert by_service["fresh-daemon"]["state"] == "ok"
        assert by_service["old-daemon"]["state"] in {"stale", "dead"}
        assert sum(payload["summary"].values()) == 3

        filtered = client.get("/api/health/sla", params={"service": "fresh-daemon"})
        assert filtered.status_code == 200
        assert len(filtered.json()["services"]) == 1

    print("PASS")