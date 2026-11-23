"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_never_scanned_backlog

router = APIRouter(prefix="/api", tags=["registry"])


class OldestFirstSeenResponse(BaseModel):
    server_id: str
    name: str | None = None
    first_seen: datetime | None = None
    registry_source: str | None = None


class NeverScannedSampleResponse(BaseModel):
    server_id: str
    name: str | None = None
    registry_source: str | None = None
    risk_tier: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class NeverScannedBacklogResponse(BaseModel):
    total: int
    by_source: dict[str, int]
    by_tier: dict[str, int]
    oldest_first_seen: OldestFirstSeenResponse | None = None
    sample: list[NeverScannedSampleResponse]
    generated_at: datetime


@router.get(
    "/registry/never-scanned-backlog",
    response_model=NeverScannedBacklogResponse,
)
def get_never_scanned_backlog_route(
    db: Session = Depends(get_session),
) -> NeverScannedBacklogResponse:
    return get_never_scanned_backlog(db)


def _self_test() -> None:
    from datetime import timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="unscanned-1",
                    name="Unscanned One",
                    registry_source="alpha",
                    risk_tier="high",
                    first_seen=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    last_seen=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    scan_count=0,
                    last_scanned=None,
                ),
                McpServerRegistry(
                    server_id="unscanned-2",
                    name="Unscanned Two",
                    registry_source="beta",
                    risk_tier="low",
                    first_seen=datetime(2024, 1, 3, tzinfo=timezone.utc),
                    last_seen=datetime(2024, 1, 4, tzinfo=timezone.utc),
                    scan_count=None,
                    last_scanned=None,
                ),
                McpServerRegistry(
                    server_id="scanned-with-zero-count",
                    name="Scanned With Zero Count",
                    registry_source="alpha",
                    risk_tier="medium",
                    first_seen=datetime(2024, 1, 5, tzinfo=timezone.utc),
                    last_seen=datetime(2024, 1, 6, tzinfo=timezone.utc),
                    scan_count=0,
                    last_scanned=datetime(2024, 1, 7, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="scanned-1",
                    name="Scanned One",
                    registry_source="gamma",
                    risk_tier="high",
                    first_seen=datetime(2024, 1, 8, tzinfo=timezone.utc),
                    last_seen=datetime(2024, 1, 9, tzinfo=timezone.utc),
                    scan_count=3,
                    last_scanned=datetime(2024, 1, 10, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="scanned-2",
                    name="Scanned Two",
                    registry_source="gamma",
                    risk_tier="low",
                    first_seen=datetime(2024, 1, 11, tzinfo=timezone.utc),
                    last_seen=datetime(2024, 1, 12, tzinfo=timezone.utc),
                    scan_count=1,
                    last_scanned=datetime(2024, 1, 13, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/registry/never-scanned-backlog")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 2, payload
        assert payload["by_source"] == {"alpha": 1, "beta": 1}, payload
        assert payload["by_tier"] == {"high": 1, "low": 1}, payload
        assert len(payload["sample"]) <= 50, payload

    print("PASS")


if __name__ == "__main__":
    _self_test()