from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_seen_to_scanned_lag


router = APIRouter(prefix="/api", tags=["registry"])


class SeenToScannedLagBucket(BaseModel):
    range: str
    count: int


class SeenToScannedLagServer(BaseModel):
    server_id: str
    lag_days: int


class SeenToScannedLagResponse(BaseModel):
    days: int
    never_scanned: int
    stale_scanned: int
    buckets: list[SeenToScannedLagBucket]
    worst: list[SeenToScannedLagServer]


@router.get(
    "/registry/seen-to-scanned-lag",
    response_model=SeenToScannedLagResponse,
    summary="Report registry scan lag",
)
def seen_to_scanned_lag(
    days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> SeenToScannedLagResponse:
    return SeenToScannedLagResponse.model_validate(
        get_seen_to_scanned_lag(session, days)
    )


if __name__ == "__main__":
    from datetime import datetime, timedelta

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
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    first_seen=now,
                    last_seen=now,
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="scanned-today",
                    first_seen=now,
                    last_seen=now,
                    last_scanned=now,
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="scanned-five-days-ago",
                    first_seen=now - timedelta(days=10),
                    last_seen=now,
                    last_scanned=now - timedelta(days=5),
                    scan_count=1,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/seen-to-scanned-lag", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 30
        assert payload["never_scanned"] == 1
        assert payload["stale_scanned"] == 1
        assert payload["worst"][0]["lag_days"] == 5
        assert payload["worst"][0]["server_id"] == "scanned-five-days-ago"

    print("PASS")