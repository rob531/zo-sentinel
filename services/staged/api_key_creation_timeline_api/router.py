from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey

from .logic import get_api_key_creation_timeline

router = APIRouter(prefix="/api", tags=["api-key-creation-timeline"])


class TimelinePoint(BaseModel):
    date: str
    count: int = Field(ge=0)


class OrganizationKeyCount(BaseModel):
    org_id: int
    count: int = Field(ge=0)


class ApiKeyCreationTimelineResponse(BaseModel):
    days: int = Field(ge=1, le=365)
    total: int = Field(ge=0)
    series: list[TimelinePoint]
    by_org: list[OrganizationKeyCount]


@router.get(
    "/api-keys/timeline",
    response_model=ApiKeyCreationTimelineResponse,
)
async def api_key_creation_timeline(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> ApiKeyCreationTimelineResponse:
    result = get_api_key_creation_timeline(session, days)
    if inspect.isawaitable(result):
        result = await result

    if isinstance(result, ApiKeyCreationTimelineResponse):
        return result
    if isinstance(result, dict):
        return ApiKeyCreationTimelineResponse(**result)
    if hasattr(ApiKeyCreationTimelineResponse, "model_validate"):
        return ApiKeyCreationTimelineResponse.model_validate(result)
    return ApiKeyCreationTimelineResponse.parse_obj(result)


if __name__ == "__main__":
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def register_date_trunc(dbapi_connection: Any, _: Any) -> None:
        def sqlite_date_trunc(partition: str, value: Any) -> str | None:
            del partition
            if value is None:
                return None
            return str(value)[:10]

        dbapi_connection.create_function("date_trunc", 2, sqlite_date_trunc)

    ApiKey.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.utcnow()
    seeded_rows = [
        ApiKey(
            id=1,
            org_id=101,
            key_hash="timeline-test-hash-1",
            label="key-1",
            created_at=now - timedelta(days=2),
        ),
        ApiKey(
            id=2,
            org_id=101,
            key_hash="timeline-test-hash-2",
            label="key-2",
            created_at=now - timedelta(days=1),
        ),
        ApiKey(
            id=3,
            org_id=202,
            key_hash="timeline-test-hash-3",
            label="key-3",
            created_at=now - timedelta(days=1),
        ),
        ApiKey(
            id=4,
            org_id=202,
            key_hash="timeline-test-hash-4",
            label="key-4",
            created_at=now,
        ),
    ]

    with test_session_factory() as session:
        session.add_all(seeded_rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Any:
        with test_session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/api-keys/timeline", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == len(seeded_rows)
        assert len(payload["series"]) == 3
        assert len(payload["by_org"]) == 2

    print("PASS")