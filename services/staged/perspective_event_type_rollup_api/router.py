from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

from .logic import get_event_type_rollup


router = APIRouter(prefix="/api")


class PerspectiveEventRollupItem(BaseModel):
    id: int
    perspective_id: str
    server_id: Optional[str] = None
    change_type: str
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    seen: bool
    created_at: datetime


class PerspectiveEventRollupPerspective(BaseModel):
    perspective_id: str
    count: int


class PerspectiveEventRollupResponse(BaseModel):
    total: int = 0
    unseen: int = 0
    by_change_type: dict[str, int] = Field(default_factory=dict)
    by_perspective: list[PerspectiveEventRollupPerspective] = Field(default_factory=list)
    items: list[PerspectiveEventRollupItem] = Field(default_factory=list)


@router.get(
    "/perspectives/event-type-rollup",
    response_model=PerspectiveEventRollupResponse,
)
def perspective_event_type_rollup(
    perspective_id: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1),
    limit: int = Query(default=50, ge=1),
    session: Session = Depends(get_session),
) -> PerspectiveEventRollupResponse:
    result = get_event_type_rollup(
        session,
        perspective_id=perspective_id,
        days=days,
        limit=limit,
    )
    return PerspectiveEventRollupResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as test_session:
        test_session.execute(
            insert(PerspectiveEvent),
            [
                {
                    "id": 1,
                    "perspective_id": "perspective-a",
                    "server_id": "server-1",
                    "change_type": "tier_change",
                    "old_tier": "low",
                    "new_tier": "medium",
                    "seen": True,
                    "created_at": now,
                },
                {
                    "id": 2,
                    "perspective_id": "perspective-a",
                    "server_id": "server-2",
                    "change_type": "added",
                    "old_tier": None,
                    "new_tier": "high",
                    "seen": True,
                    "created_at": now,
                },
                {
                    "id": 3,
                    "perspective_id": "perspective-b",
                    "server_id": "server-3",
                    "change_type": "tier_change",
                    "old_tier": "medium",
                    "new_tier": "high",
                    "seen": False,
                    "created_at": now,
                },
                {
                    "id": 4,
                    "perspective_id": "perspective-b",
                    "server_id": "server-4",
                    "change_type": "added",
                    "old_tier": None,
                    "new_tier": "medium",
                    "seen": True,
                    "created_at": now,
                },
            ],
        )
        test_session.commit()

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/perspectives/event-type-rollup")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["unseen"] == 1
    assert payload["by_change_type"]["tier_change"] == 2
    print("PASS")