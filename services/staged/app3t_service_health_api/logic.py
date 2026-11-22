"""Service health data/computation layer using the real application database."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ServiceHealthItem(BaseModel):
    service: str
    status: str
    age_seconds: Optional[int]
    is_stale: bool


class ServiceHealthResponse(BaseModel):
    checked_at: str
    services: List[ServiceHealthItem]
    healthy: int
    stale: int
    total: int


router = APIRouter(prefix="/api")


def _as_utc(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_service_health(
    db: Session,
    stale_after_seconds: int = 600,
    service: Optional[str] = None,
) -> ServiceHealthResponse:
    """Read service heartbeat rows and compute current liveness information."""
    metadata = MetaData()
    service_health = Table(
        "service_health",
        metadata,
        autoload_with=db.get_bind(),
    )

    statement = select(
        service_health.c.service,
        service_health.c.status,
        service_health.c.last_heartbeat,
        service_health.c.meta,
    )
    if service is not None:
        statement = statement.where(service_health.c.service == service)

    rows = db.execute(statement).all()
    now = datetime.now(timezone.utc)
    items: List[ServiceHealthItem] = []

    for row in rows:
        heartbeat = _as_utc(row.last_heartbeat)
        age_seconds: Optional[int] = None
        if heartbeat is not None:
            age_seconds = max(0, int((now - heartbeat).total_seconds()))

        stored_status = str(row.status)
        heartbeat_is_old = (
            age_seconds is not None and age_seconds > stale_after_seconds
        )
        is_stale = stored_status == "stale" or (
            stored_status == "running" and heartbeat_is_old
        )

        items.append(
            ServiceHealthItem(
                service=str(row.service),
                status=stored_status,
                age_seconds=age_seconds,
                is_stale=is_stale,
            )
        )

    healthy = sum(
        1 for item in items if item.status == "running" and not item.is_stale
    )
    stale = sum(1 for item in items if item.is_stale)

    return ServiceHealthResponse(
        checked_at=now.isoformat().replace("+00:00", "Z"),
        services=items,
        healthy=healthy,
        stale=stale,
        total=len(items),
    )


@router.get("/service_health", response_model=ServiceHealthResponse)
def service_health_endpoint(
    stale_after_seconds: int = Query(default=600, ge=0),
    service: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> ServiceHealthResponse:
    return get_service_health(
        db=db,
        stale_after_seconds=stale_after_seconds,
        service=service,
    )


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_metadata = MetaData()
    test_service_health = Table(
        "service_health",
        test_metadata,
        Column("service", String, primary_key=True),
        Column("status", String, nullable=False),
        Column("last_heartbeat", DateTime),
        Column("meta", Text),
    )
    test_metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.execute(
            test_service_health.insert(),
            [
                {
                    "service": "consumer",
                    "status": "running",
                    "last_heartbeat": datetime.utcnow() - timedelta(seconds=5),
                    "meta": None,
                },
                {
                    "service": "advisor",
                    "status": "running",
                    "last_heartbeat": datetime.utcnow() - timedelta(seconds=15),
                    "meta": None,
                },
                {
                    "service": "scorer",
                    "status": "running",
                    "last_heartbeat": datetime.utcnow() - timedelta(seconds=1200),
                    "meta": None,
                },
                {
                    "service": "bootstrap",
                    "status": "starting",
                    "last_heartbeat": datetime.utcnow() - timedelta(seconds=5),
                    "meta": None,
                },
            ],
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    response = client.get(
        "/api/service_health",
        params={"stale_after_seconds": 600},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 4
    assert payload["healthy"] == 2
    assert payload["stale"] == 1
    stale_row = next(item for item in payload["services"] if item["service"] == "scorer")
    assert stale_row["is_stale"] is True
    assert stale_row["age_seconds"] > 600
    starting_row = next(
        item for item in payload["services"] if item["service"] == "bootstrap"
    )
    assert starting_row["status"] == "starting"

    print("PASS")