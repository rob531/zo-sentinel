from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_service_health_inventory


class ServiceHealthItem(BaseModel):
    service: str
    status: str
    last_heartbeat: datetime
    age_seconds: float
    stale: bool


class ServiceHealthInventory(BaseModel):
    total_services: int
    services: list[ServiceHealthItem] = Field(default_factory=list)
    by_status: dict[str, int] = Field(default_factory=dict)


router = APIRouter(prefix="/api", tags=["service-health"])


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "_mapping"):
        return dict(value._mapping)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {
        "service": getattr(value, "service", ""),
        "status": getattr(value, "status", ""),
        "last_heartbeat": getattr(value, "last_heartbeat", None),
        "age_seconds": getattr(value, "age_seconds", 0),
        "stale": getattr(value, "stale", False),
    }


def _parse_heartbeat(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Invalid last_heartbeat value")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.get("/service-health", response_model=ServiceHealthInventory)
def read_service_health(
    status: str | None = Query(default=None),
    max_age_seconds: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ServiceHealthInventory:
    now = datetime.now(timezone.utc)
    inventory = _as_dict(
        get_service_health_inventory(
            session=session,
            status=status,
            max_age_seconds=0,
        )
    )

    services: list[ServiceHealthItem] = []
    for raw_item in inventory.get("services", []):
        item = _as_dict(raw_item)
        heartbeat = _parse_heartbeat(item["last_heartbeat"])
        age_seconds = max(0.0, (now - heartbeat).total_seconds())

        if status is not None and item["status"] != status:
            continue
        if max_age_seconds > 0 and age_seconds > max_age_seconds:
            continue

        services.append(
            ServiceHealthItem(
                service=str(item["service"]),
                status=str(item["status"]),
                last_heartbeat=heartbeat,
                age_seconds=age_seconds,
                stale=bool(item.get("stale", age_seconds > 600)),
            )
        )

    services.sort(key=lambda item: item.age_seconds, reverse=True)
    by_status: dict[str, int] = {}
    for item in services:
        by_status[item.status] = by_status.get(item.status, 0) + 1

    return ServiceHealthInventory(
        total_services=len(services),
        services=services,
        by_status=by_status,
    )


if __name__ == "__main__":
    import json
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as seed_session:
        seed_session.execute(
            text(
                """
                CREATE TABLE service_health (
                    service VARCHAR(255) PRIMARY KEY,
                    last_heartbeat TIMESTAMP NOT NULL,
                    status VARCHAR(64) NOT NULL,
                    meta TEXT
                )
                """
            )
        )
        current_time = datetime.now(timezone.utc)
        seed_session.execute(
            text(
                """
                INSERT INTO service_health (service, last_heartbeat, status, meta)
                VALUES (:service, :last_heartbeat, :status, :meta)
                """
            ),
            [
                {
                    "service": "fresh-daemon",
                    "last_heartbeat": current_time - timedelta(seconds=5),
                    "status": "healthy",
                    "meta": json.dumps({"version": "1.0.0"}),
                },
                {
                    "service": "stale-daemon",
                    "last_heartbeat": current_time - timedelta(seconds=1200),
                    "status": "running",
                    "meta": json.dumps({"stale_after_seconds": 600}),
                },
                {
                    "service": "degraded-daemon",
                    "last_heartbeat": current_time - timedelta(seconds=60),
                    "status": "degraded",
                    "meta": json.dumps({"version": "1.0.0"}),
                },
            ],
        )
        seed_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/service-health")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_services"] == 3, payload

    stale_service = next(
        item for item in payload["services"] if item["service"] == "stale-daemon"
    )
    assert stale_service["stale"] is True, payload
    assert stale_service["age_seconds"] > 600, payload
    assert set(payload["by_status"]) == {"healthy", "running", "degraded"}, payload

    print("PASS")