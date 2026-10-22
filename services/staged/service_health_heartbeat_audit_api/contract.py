from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import get_session
from app.models import McpServerRegistry  # noqa: F401
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session


class HeartbeatService(BaseModel):
    service: str
    status: str
    age_seconds: float
    stale: bool
    meta: Any = None


class HeartbeatAuditResponse(BaseModel):
    services: list[HeartbeatService]
    stale_count: int
    total: int


def _parse_threshold(raw: str) -> tuple[float, dict[str, float]]:
    default = 300.0
    overrides: dict[str, float] = {}
    value = raw.strip()

    if not value:
        return default, overrides

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = None

    if isinstance(decoded, dict):
        for service, threshold in decoded.items():
            try:
                overrides[str(service)] = max(0.0, float(threshold))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="Invalid service threshold") from exc
        return default, overrides

    if isinstance(decoded, (int, float)) and not isinstance(decoded, bool):
        return max(0.0, float(decoded)), overrides

    if "," in value or ";" in value:
        separator = "," if "," in value else ";"
        for item in value.split(separator):
            if ":" not in item:
                raise HTTPException(status_code=422, detail="Invalid threshold map")
            service, threshold = item.split(":", 1)
            try:
                overrides[service.strip()] = max(0.0, float(threshold))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid service threshold") from exc
        return default, overrides

    try:
        return max(0.0, float(value)), overrides
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid threshold") from exc


def _normalize_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="Invalid last_heartbeat timestamp") from exc
    else:
        raise HTTPException(status_code=500, detail="Invalid last_heartbeat timestamp")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_meta(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def create_contract_app() -> FastAPI:
    that_app = FastAPI()

    @that_app.get("/api/ops/heartbeats", response_model=HeartbeatAuditResponse)
    def list_heartbeats(
        threshold: str = Query(default="300"),
        session: Session = Depends(get_session),
    ) -> HeartbeatAuditResponse:
        default_threshold, threshold_overrides = _parse_threshold(threshold)
        rows = session.execute(
            text("SELECT service, last_heartbeat, status, meta FROM service_health")
        ).mappings()

        now = datetime.now(timezone.utc)
        services: list[HeartbeatService] = []

        for row in rows:
            service = str(row["service"])
            last_heartbeat = _normalize_timestamp(row["last_heartbeat"])
            age_seconds = max(0.0, (now - last_heartbeat).total_seconds())
            service_threshold = threshold_overrides.get(service, default_threshold)
            services.append(
                HeartbeatService(
                    service=service,
                    status=str(row["status"] or "unknown"),
                    age_seconds=age_seconds,
                    stale=age_seconds > service_threshold,
                    meta=_normalize_meta(row["meta"]),
                )
            )

        services.sort(key=lambda item: item.service)
        return HeartbeatAuditResponse(
            services=services,
            stale_count=sum(1 for item in services if item.stale),
            total=len(services),
        )

    return that_app


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT PRIMARY KEY,
                    last_heartbeat TEXT NOT NULL,
                    status TEXT NOT NULL,
                    meta TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO service_health (service, last_heartbeat, status, meta)
                VALUES (:service, :last_heartbeat, :status, :meta)
                """
            ),
            [
                {
                    "service": "fresh-api",
                    "last_heartbeat": now.isoformat(),
                    "status": "healthy",
                    "meta": json.dumps({"region": "local"}),
                },
                {
                    "service": "old-worker",
                    "last_heartbeat": (now - timedelta(minutes=10)).isoformat(),
                    "status": "healthy",
                    "meta": json.dumps({"region": "local"}),
                },
                {
                    "service": "degraded-database",
                    "last_heartbeat": (now - timedelta(minutes=20)).isoformat(),
                    "status": "degraded",
                    "meta": json.dumps({"region": "local"}),
                },
            ],
        )

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app = create_contract_app()
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/ops/heartbeats", params={"threshold": 300})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3, payload
        assert payload["stale_count"] == 2, payload
        assert len(payload["services"]) == 3, payload
        assert sum(1 for item in payload["services"] if item["stale"]) == 2, payload
        fresh = next(item for item in payload["services"] if item["service"] == "fresh-api")
        assert fresh["stale"] is False, payload
        degraded = next(
            item for item in payload["services"] if item["service"] == "degraded-database"
        )
        assert degraded["status"] == "degraded", payload

    print("PASS")


if __name__ == "__main__":
    _run_self_test()