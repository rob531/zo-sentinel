from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_last_seen_staleness

router = APIRouter(prefix="/api")


class StalenessBucket(BaseModel):
    bucket: str
    count: int = Field(ge=0)


class RegistrySourceStaleness(BaseModel):
    registry_source: str
    buckets: list[StalenessBucket]


class LastSeenStalenessResponse(BaseModel):
    as_of: datetime
    total_servers: int = Field(ge=0)
    buckets: list[StalenessBucket]
    by_source: list[RegistrySourceStaleness]


@router.get(
    "/registry/last-seen-staleness",
    response_model=LastSeenStalenessResponse,
)
def registry_last_seen_staleness(
    session: Session = Depends(get_session),
) -> LastSeenStalenessResponse:
    return get_last_seen_staleness(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.utcnow()
    seeded_rows = [
        {
            "server_id": "server-0-5d",
            "name": "server-0-5d",
            "registry_source": "alpha",
            "risk_tier": "low",
            "verdict": "approved",
            "first_seen": now - timedelta(days=2),
            "last_seen": now - timedelta(hours=12),
            "last_scanned": now - timedelta(hours=12),
            "scan_count": 1,
        },
        {
            "server_id": "server-3d",
            "name": "server-3d",
            "registry_source": "alpha",
            "risk_tier": "medium",
            "verdict": "conditional",
            "first_seen": now - timedelta(days=10),
            "last_seen": now - timedelta(days=3),
            "last_scanned": now - timedelta(days=3),
            "scan_count": 2,
        },
        {
            "server_id": "server-15d",
            "name": "server-15d",
            "registry_source": "beta",
            "risk_tier": "medium",
            "verdict": "conditional",
            "first_seen": now - timedelta(days=20),
            "last_seen": now - timedelta(days=15),
            "last_scanned": now - timedelta(days=15),
            "scan_count": 3,
        },
        {
            "server_id": "server-45d",
            "name": "server-45d",
            "registry_source": "beta",
            "risk_tier": "high",
            "verdict": "rejected",
            "first_seen": now - timedelta(days=60),
            "last_seen": now - timedelta(days=45),
            "last_scanned": now - timedelta(days=45),
            "scan_count": 4,
        },
        {
            "server_id": "server-120d",
            "name": "server-120d",
            "registry_source": "gamma",
            "risk_tier": "high",
            "verdict": "rejected",
            "first_seen": now - timedelta(days=150),
            "last_seen": now - timedelta(days=120),
            "last_scanned": now - timedelta(days=120),
            "scan_count": 5,
        },
    ]

    with TestSession() as session:
        session.execute(McpServerRegistry.__table__.insert(), seeded_rows)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/last-seen-staleness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == len(seeded_rows)
        assert sum(item["count"] for item in payload["buckets"]) == len(seeded_rows)
        assert len(payload["buckets"]) == 5
        assert all(item["count"] == 1 for item in payload["buckets"])
        assert sum(
            sum(bucket["count"] for bucket in source["buckets"])
            for source in payload["by_source"]
        ) == len(seeded_rows)
        assert all(
            len(source["buckets"]) == 5
            and sum(bucket["count"] for bucket in source["buckets"])
            == source["buckets"][0]["count"]
            + sum(bucket["count"] for bucket in source["buckets"][1:])
            for source in payload["by_source"]
        )

    test_engine.dispose()
    print("PASS")