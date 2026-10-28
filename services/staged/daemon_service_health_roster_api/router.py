from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_service_health_roster

router = APIRouter()


class ServiceHealthEntry(BaseModel):
    service: str
    status: str
    last_heartbeat: Optional[datetime] = None
    age_seconds: Optional[float] = None
    stale: bool = False
    meta: Any = None


class ServiceHealthCounts(BaseModel):
    ok: int = 0
    stale: int = 0
    other: int = 0


class ServiceHealthRoster(BaseModel):
    services: List[ServiceHealthEntry] = Field(default_factory=list)
    counts: ServiceHealthCounts = Field(default_factory=ServiceHealthCounts)
    total: int = 0


def _extract_rows(result: Any) -> List[Dict[str, Any]]:
    if isinstance(result, list):
        return [dict(row) for row in result]
    if isinstance(result, dict):
        for key in ("services", "rows", "items"):
            rows = result.get(key)
            if isinstance(rows, list):
                return [dict(row) for row in rows]
    raise ValueError("service-health logic returned no service rows")


def _build_counts(rows: List[Dict[str, Any]]) -> ServiceHealthCounts:
    ok = 0
    stale = 0
    other = 0

    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        is_stale = bool(row.get("stale", False))

        if status == "ok":
            ok += 1
        elif status == "stale":
            stale += 1
        else:
            other += 1

        if is_stale and status != "stale":
            stale += 1

    return ServiceHealthCounts(ok=ok, stale=stale, other=other)


@router.get(
    "/ops/service-health",
    response_model=ServiceHealthRoster,
    summary="Get daemon service health roster",
)
def service_health_roster(
    max_age_seconds: int = Query(default=600, ge=0),
    session: Session = Depends(get_session),
) -> ServiceHealthRoster:
    result = get_service_health_roster(session, max_age_seconds)
    rows = _extract_rows(result)
    return ServiceHealthRoster(
        services=[ServiceHealthEntry(**row) for row in rows],
        counts=_build_counts(rows),
        total=len(rows),
    )


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, JSON, MetaData, String, Table, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    metadata = MetaData()
    service_health = Table(
        "service_health",
        metadata,
        Column("service", String, primary_key=True),
        Column("status", String, nullable=False),
        Column("last_heartbeat", DateTime, nullable=True),
        Column("meta", JSON, nullable=True),
    )
    metadata.create_all(engine)

    now = datetime.utcnow()
    with TestingSession() as db:
        db.execute(
            insert(service_health),
            [
                {
                    "service": "fresh-daemon",
                    "status": "ok",
                    "last_heartbeat": now - timedelta(seconds=10),
                    "meta": {"pid": 101},
                },
                {
                    "service": "stale-daemon",
                    "status": "ok",
                    "last_heartbeat": now - timedelta(seconds=1200),
                    "meta": {"pid": 102},
                },
                {
                    "service": "failed-daemon",
                    "status": "error",
                    "last_heartbeat": now - timedelta(seconds=30),
                    "meta": {"pid": 103},
                },
            ],
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/ops/service-health", params={"max_age_seconds": 600})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["counts"]["ok"] == 2

        by_service = {entry["service"]: entry for entry in payload["services"]}
        assert by_service["fresh-daemon"]["stale"] is False
        assert by_service["stale-daemon"]["stale"] is True

    print("PASS")