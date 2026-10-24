from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry


class ServiceHealthCounts(BaseModel):
    ok: int
    stale: int
    other: int


class ServiceHealthEntry(BaseModel):
    service: str
    status: str
    last_heartbeat: Optional[str]
    age_seconds: Optional[float]
    stale: bool
    meta: Dict[str, Any]


class ServiceHealthRoster(BaseModel):
    generated_at: str
    stale_threshold_seconds: int
    counts: ServiceHealthCounts
    services: List[ServiceHealthEntry]


router = APIRouter(prefix="/api", tags=["service-health"])


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        if text_value.endswith("Z"):
            text_value = text_value[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text_value)
        except ValueError:
            return None
    return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return _utc(value).isoformat()


def _normalize_meta(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def build_roster(rows: list[dict], now: datetime, threshold: int) -> dict:
    normalized_now = _utc(now)
    services: list[dict[str, Any]] = []
    ok_count = 0
    stale_count = 0
    other_count = 0

    for row in rows:
        service = str(row.get("service") or "")
        status = str(row.get("status") or "")
        heartbeat = _coerce_datetime(row.get("last_heartbeat"))
        meta = _normalize_meta(row.get("meta"))

        if heartbeat is None:
            age_seconds = None
            stale = True
        else:
            age_seconds = float((normalized_now - _utc(heartbeat)).total_seconds())
            stale = age_seconds > threshold

        if stale:
            stale_count += 1
        elif status.strip().lower() == "ok":
            ok_count += 1
        else:
            other_count += 1

        services.append(
            {
                "service": service,
                "status": status,
                "last_heartbeat": _isoformat(heartbeat) if heartbeat is not None else None,
                "age_seconds": age_seconds,
                "stale": stale,
                "meta": meta,
            }
        )

    return {
        "generated_at": _isoformat(normalized_now),
        "stale_threshold_seconds": threshold,
        "counts": {
            "ok": ok_count,
            "stale": stale_count,
            "other": other_count,
        },
        "services": services,
    }


@router.get("/ops/service-health", response_model=ServiceHealthRoster)
async def get_service_health_roster(session=Depends(get_session)) -> ServiceHealthRoster:
    result = session.execute(
        text(
            """
            SELECT service, status, last_heartbeat, meta
            FROM service_health
            ORDER BY service
            """
        )
    )
    if inspect.isawaitable(result):
        result = await result

    rows = [dict(mapping) for mapping in result.mappings()]
    return ServiceHealthRoster(
        **build_roster(rows, datetime.now(timezone.utc), threshold=300)
    )


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with Session(test_engine) as setup_session:
        setup_session.execute(
            text(
                """
                CREATE TABLE service_health (
                    service VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    last_heartbeat DATETIME NULL,
                    meta JSON NULL
                )
                """
            )
        )

        seeded_at = datetime.now(timezone.utc)
        setup_session.execute(
            text(
                """
                INSERT INTO service_health (service, status, last_heartbeat, meta)
                VALUES (:service, :status, :last_heartbeat, :meta)
                """
            ),
            [
                {
                    "service": "fresh-daemon",
                    "status": "ok",
                    "last_heartbeat": (seeded_at - timedelta(seconds=10)).isoformat(),
                    "meta": json.dumps({"pid": 101}),
                },
                {
                    "service": "stale-daemon",
                    "status": "ok",
                    "last_heartbeat": (seeded_at - timedelta(minutes=10)).isoformat(),
                    "meta": json.dumps({"pid": 102}),
                },
                {
                    "service": "error-daemon",
                    "status": "error",
                    "last_heartbeat": (seeded_at - timedelta(seconds=30)).isoformat(),
                    "meta": json.dumps({"error": "probe failed"}),
                },
            ],
        )
        setup_session.commit()

    def override_get_session():
        with Session(test_engine) as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/ops/service-health")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["counts"]["stale"] == 1, payload
        stale_entry = next(
            entry for entry in payload["services"] if entry["service"] == "stale-daemon"
        )
        assert stale_entry["stale"] is True, payload
        expected_keys = {
            "service",
            "status",
            "last_heartbeat",
            "age_seconds",
            "stale",
            "meta",
        }
        assert all(set(entry) == expected_keys for entry in payload["services"]), payload

    print("PASS")