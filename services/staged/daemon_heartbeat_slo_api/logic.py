from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["ops"])


class ServiceHeartbeatSLOItem(BaseModel):
    service: str
    status: str
    age_seconds: float | None
    last_heartbeat: str | None
    meta: Any = None


class ServiceHeartbeatSLOResponse(BaseModel):
    generated_at: str
    threshold_seconds: int
    services: list[ServiceHeartbeatSLOItem]
    counts: dict[str, int]
    stale_services: list[str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        try:
            result = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None

        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"

        try:
            result = datetime.fromisoformat(raw)
        except ValueError:
            try:
                result = datetime.fromtimestamp(float(raw), tz=timezone.utc)
            except (TypeError, ValueError, OverflowError, OSError):
                return None
    else:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _normalise_meta(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _rows_from_result(result: Any) -> list[dict[str, Any]]:
    if hasattr(result, "mappings"):
        return [dict(row) for row in result.mappings().all()]

    rows = list(result)
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [dict(row) for row in rows]
    return [
        {
            "service": row[0] if len(row) > 0 else None,
            "status": row[1] if len(row) > 1 else None,
            "last_heartbeat": row[2] if len(row) > 2 else None,
            "meta": row[3] if len(row) > 3 else None,
        }
        for row in rows
    ]


def compute_heartbeat_slo(
    session: Session,
    threshold_seconds: int = 300,
    now: datetime | None = None,
) -> ServiceHeartbeatSLOResponse:
    result = session.execute(text("SELECT * FROM service_health"))
    rows = _rows_from_result(result)

    current_time = now or _utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    services: list[ServiceHeartbeatSLOItem] = []
    counts = {"ok": 0, "stale": 0, "missing": 0}
    stale_services: list[str] = []

    for row in rows:
        service_name = row.get("service")
        if service_name is None:
            continue
        service_name = str(service_name)

        heartbeat = _as_datetime(row.get("last_heartbeat"))
        if heartbeat is None:
            status = "missing"
            age_seconds = None
            last_heartbeat = None
        else:
            age_seconds = round((current_time - heartbeat).total_seconds(), 3)
            if age_seconds < 0:
                age_seconds = 0.0
            status = "stale" if age_seconds > threshold_seconds else "ok"
            last_heartbeat = heartbeat.isoformat()

        if status == "stale":
            counts["stale"] += 1
            stale_services.append(service_name)
        else:
            counts[status] += 1

        services.append(
            ServiceHeartbeatSLOItem(
                service=service_name,
                status=status,
                age_seconds=age_seconds,
                last_heartbeat=last_heartbeat,
                meta=_normalise_meta(row.get("meta")),
            )
        )

    services.sort(key=lambda item: item.service)
    stale_services.sort()

    return ServiceHeartbeatSLOResponse(
        generated_at=current_time.isoformat(),
        threshold_seconds=threshold_seconds,
        services=services,
        counts=counts,
        stale_services=stale_services,
    )


def get_heartbeat_slo(
    session: Session,
    threshold_seconds: int = 300,
) -> ServiceHeartbeatSLOResponse:
    return compute_heartbeat_slo(session, threshold_seconds=threshold_seconds)


def get_heartbeat_slo_data(
    session: Session,
    threshold_seconds: int = 300,
) -> ServiceHeartbeatSLOResponse:
    return compute_heartbeat_slo(session, threshold_seconds=threshold_seconds)


def build_heartbeat_slo_payload(
    session: Session,
    threshold_seconds: int = 300,
) -> ServiceHeartbeatSLOResponse:
    return compute_heartbeat_slo(session, threshold_seconds=threshold_seconds)


@router.get(
    "/ops/heartbeat-slo",
    response_model=ServiceHeartbeatSLOResponse,
    status_code=200,
)
def heartbeat_slo_endpoint(
    threshold: str = Query(default="300"),
    session: Session = Depends(get_session),
) -> ServiceHeartbeatSLOResponse:
    try:
        threshold_seconds = int(threshold)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="threshold must be an integer")

    return compute_heartbeat_slo(session, threshold_seconds=threshold_seconds)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    BaseMetadata = McpServerRegistry.metadata
    service_health = McpServerRegistry.metadata.tables.get("service_health")
    if service_health is None:
        from sqlalchemy import Column, DateTime, MetaData, String, Table, Text

        metadata = MetaData()
        service_health = Table(
            "service_health",
            metadata,
            Column("service", String, primary_key=True),
            Column("status", String),
            Column("last_heartbeat", DateTime),
            Column("meta", Text),
        )
    else:
        service_health.create(bind=engine, checkfirst=True)

    if service_health.metadata is not McpServerRegistry.metadata:
        service_health.create(bind=engine, checkfirst=True)

    now = _utc_now()
    with TestingSession() as session:
        session.execute(
            text(
                "INSERT INTO service_health "
                "(service, status, last_heartbeat, meta) "
                "VALUES (:service, :status, :last_heartbeat, :meta)"
            ),
            [
                {
                    "service": "fresh-daemon",
                    "status": "ok",
                    "last_heartbeat": now.replace(microsecond=0),
                    "meta": "{}",
                },
                {
                    "service": "old-daemon",
                    "status": "ok",
                    "last_heartbeat": now.replace(microsecond=0),
                    "meta": "{}",
                },
            ],
        )
        session.commit()
        session.execute(
            text("UPDATE service_health SET last_heartbeat = :heartbeat WHERE service = 'fresh-daemon'"),
            {"heartbeat": now.replace(microsecond=0) - __import__("datetime").timedelta(seconds=10)},
        )
        session.execute(
            text("UPDATE service_health SET last_heartbeat = :heartbeat WHERE service = 'old-daemon'"),
            {"heartbeat": now.replace(microsecond=0) - __import__("datetime").timedelta(seconds=4000)},
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/ops/heartbeat-slo")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts"]["stale"] == 1
    assert payload["counts"]["ok"] == 1
    assert "old-daemon" in payload["stale_services"]
    print("PASS")