from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import FreshnessStat

from .logic import get_service_health

router = APIRouter(prefix="/api", tags=["service-health"])


class ServiceHealthEntry(BaseModel):
    service: str
    last_heartbeat: Optional[str] = None
    status: Optional[str] = None
    age_seconds: Optional[int] = None
    stale: bool


class ServiceHealthResponse(BaseModel):
    generated_at: str
    stale_after_seconds: int
    services: list[ServiceHealthEntry]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_heartbeat(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
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
            candidate = f"{candidate[:-1]}+00:00"
        try:
            return _as_utc(datetime.fromisoformat(candidate))
        except ValueError:
            return None
    return None


def _read_service_health(session: Any, stale_after_seconds: int) -> ServiceHealthResponse:
    result = session.execute(
        text(
            """
            SELECT service, last_heartbeat, status, meta
            FROM service_health
            """
        )
    )
    now = datetime.now(timezone.utc)
    services: list[ServiceHealthEntry] = []

    for row in result.mappings():
        heartbeat = _parse_heartbeat(row["last_heartbeat"])
        if heartbeat is None:
            age_seconds = None
            stale = True
            last_heartbeat = None
        else:
            age_seconds = max(0, int((now - heartbeat).total_seconds()))
            stale = age_seconds > stale_after_seconds
            last_heartbeat = heartbeat.isoformat()

        raw_status = row["status"]
        status = None if raw_status is None else str(raw_status)
        raw_service = row["service"]
        service = "" if raw_service is None else str(raw_service)

        services.append(
            ServiceHealthEntry(
                service=service,
                last_heartbeat=last_heartbeat,
                status=status,
                age_seconds=age_seconds,
                stale=stale,
            )
        )

    services.sort(key=lambda entry: entry.service)

    return ServiceHealthResponse(
        generated_at=now.isoformat(),
        stale_after_seconds=stale_after_seconds,
        services=services,
    )


@router.get("/service-health", response_model=ServiceHealthResponse)
def service_health(
    stale_after_seconds: int = Query(default=300, ge=0),
    session: Any = Depends(get_session),
) -> ServiceHealthResponse:
    return _read_service_health(session, stale_after_seconds)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.execute(
            text(
                """
                CREATE TABLE service_health (
                    service TEXT NOT NULL,
                    last_heartbeat TEXT,
                    status TEXT,
                    meta TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        session.execute(
            text(
                """
                INSERT INTO service_health
                    (service, last_heartbeat, status, meta)
                VALUES
                    (:service, :last_heartbeat, :status, :meta)
                """
            ),
            [
                {
                    "service": "zeta",
                    "last_heartbeat": None,
                    "status": "running",
                    "meta": None,
                },
                {
                    "service": "alpha",
                    "last_heartbeat": (now.replace(microsecond=0) - _timedelta(hours=1)).isoformat(),
                    "status": "running",
                    "meta": None,
                },
                {
                    "service": "middle",
                    "last_heartbeat": (now.replace(microsecond=0) - _timedelta(seconds=10)).isoformat(),
                    "status": "running",
                    "meta": None,
                },
            ],
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/service-health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["stale_after_seconds"] == 300
        assert len(payload["services"]) == 3
        assert [entry["service"] for entry in payload["services"]] == [
            "alpha",
            "middle",
            "zeta",
        ]

        by_service = {entry["service"]: entry for entry in payload["services"]}
        assert by_service["alpha"]["stale"] is True
        assert by_service["alpha"]["age_seconds"] >= 3600
        assert by_service["middle"]["stale"] is False

    print("PASS")


def _timedelta(**kwargs: int):
    from datetime import timedelta

    return timedelta(**kwargs)


if __name__ == "__main__":
    _self_test()