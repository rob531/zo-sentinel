from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import JSON, Column, DateTime, String, Table, MetaData, create_engine, insert, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


STALE_THRESHOLD_SECONDS = 300
SERVICE_HEALTH_SQL = text(
    """
    SELECT service, status, last_heartbeat, meta
    FROM service_health
    ORDER BY service
    """
)

router = APIRouter(prefix="/api")
app = FastAPI()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return _as_utc(datetime.fromisoformat(candidate))
        except ValueError:
            return None
    return None


def _normalize_meta(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(decoded, dict):
            return decoded
    return {}


def build_roster(rows: list[dict], now: datetime, threshold: int) -> dict:
    current_time = _as_utc(now)
    services = []
    counts = {"ok": 0, "stale": 0, "other": 0}

    for row in rows:
        heartbeat = _parse_datetime(row.get("last_heartbeat"))
        age_seconds = (
            float((current_time - heartbeat).total_seconds())
            if heartbeat is not None
            else None
        )
        stale = age_seconds is None or age_seconds > threshold
        status = str(row.get("status") or "")

        if stale:
            counts["stale"] += 1
        elif status.lower() == "ok":
            counts["ok"] += 1
        else:
            counts["other"] += 1

        services.append(
            {
                "service": str(row.get("service") or ""),
                "status": status,
                "last_heartbeat": heartbeat.isoformat() if heartbeat else None,
                "age_seconds": age_seconds,
                "stale": stale,
                "meta": _normalize_meta(row.get("meta")),
            }
        )

    return {
        "generated_at": current_time.isoformat(),
        "stale_threshold_seconds": threshold,
        "counts": counts,
        "services": services,
    }


def _read_service_health(session: Any) -> list[dict]:
    result = session.execute(SERVICE_HEALTH_SQL)
    return [dict(row) for row in result.mappings()]


@router.get("/ops/service-health")
def get_service_health_roster(session=Depends(get_session)) -> dict:
    return build_roster(
        rows=_read_service_health(session),
        now=datetime.now(timezone.utc),
        threshold=STALE_THRESHOLD_SECONDS,
    )


app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    service_health = Table(
        "service_health",
        metadata,
        Column("service", String),
        Column("status", String),
        Column("last_heartbeat", DateTime),
        Column("meta", JSON),
    )
    metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.execute(
            insert(service_health),
            [
                {
                    "service": "fresh-service",
                    "status": "ok",
                    "last_heartbeat": now - timedelta(seconds=30),
                    "meta": {"region": "local"},
                },
                {
                    "service": "stale-service",
                    "status": "ok",
                    "last_heartbeat": now - timedelta(minutes=10),
                    "meta": {"region": "local"},
                },
                {
                    "service": "error-service",
                    "status": "error",
                    "last_heartbeat": now - timedelta(seconds=15),
                    "meta": {"detail": "failed"},
                },
            ],
        )
        session.commit()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/ops/service-health")
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["stale_threshold_seconds"] == 300
            assert payload["counts"]["stale"] == 1, payload
            assert payload["counts"]["ok"] == 1, payload
            assert payload["counts"]["other"] == 1, payload

            expected_keys = {
                "service",
                "status",
                "last_heartbeat",
                "age_seconds",
                "stale",
                "meta",
            }
            assert len(payload["services"]) == 3, payload
            assert all(
                set(entry) == expected_keys for entry in payload["services"]
            ), payload

            stale_entries = [
                entry for entry in payload["services"] if entry["stale"]
            ]
            assert len(stale_entries) == 1, payload
            assert stale_entries[0]["service"] == "stale-service", payload
            assert stale_entries[0]["age_seconds"] > 300, payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()