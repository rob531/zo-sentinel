from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent
from .logic import get_change_cadence

router = APIRouter(prefix="/api", tags=["perspectives"])


class TierTransition(BaseModel):
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    count: int = Field(ge=0)


class PerspectiveChangeCadence(BaseModel):
    perspective_id: str
    name: str
    event_count: int = Field(ge=0)
    by_change_type: Dict[str, int] = Field(default_factory=dict)
    tier_transitions: List[TierTransition] = Field(default_factory=list)
    unseen_count: int = Field(ge=0)


class ChangeCadenceResponse(BaseModel):
    days: int = Field(ge=1)
    total_events: int = Field(ge=0)
    perspectives: List[PerspectiveChangeCadence] = Field(default_factory=list)


@router.get(
    "/perspectives/change-cadence",
    response_model=ChangeCadenceResponse,
    summary="Get perspective change cadence",
)
def get_perspective_change_cadence(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> ChangeCadenceResponse:
    return get_change_cadence(session=session, days=days)


def _run_self_test() -> None:
    from datetime import datetime, timedelta

    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)

    now = datetime.utcnow()
    with Session(engine) as session:
        session.add_all(
            [
                Perspective(
                    id="perspective-1",
                    name="First Perspective",
                    created_by="self-test",
                    description="Self-test perspective",
                    facet_filters={},
                ),
                Perspective(
                    id="perspective-2",
                    name="Second Perspective",
                    created_by="self-test",
                    description="Self-test perspective",
                    facet_filters={},
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="medium",
                    seen=True,
                    created_at=now - timedelta(days=1),
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-1",
                    server_id="server-2",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="medium",
                    seen=False,
                    created_at=now - timedelta(days=2),
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id="perspective-2",
                    server_id="server-3",
                    change_type="tier_change",
                    old_tier="medium",
                    new_tier="high",
                    seen=True,
                    created_at=now - timedelta(days=10),
                ),
                PerspectiveEvent(
                    id=4,
                    perspective_id="perspective-2",
                    server_id="server-4",
                    change_type="status_change",
                    old_tier="none",
                    new_tier="low",
                    seen=True,
                    created_at=now - timedelta(days=20),
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            with Session(engine) as test_session:
                yield test_session

        test_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(test_app).get(
            "/api/perspectives/change-cadence",
            params={"days": 7},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 7
        assert payload["total_events"] == 2

        first = next(
            item
            for item in payload["perspectives"]
            if item["perspective_id"] == "perspective-1"
        )
        assert first["event_count"] == 2
        assert first["unseen_count"] == 1
        assert first["by_change_type"]["tier_change"] == 2
        assert any(
            transition["old_tier"] == "low"
            and transition["new_tier"] == "medium"
            and transition["count"] == 2
            for transition in first["tier_transitions"]
        )

    print("PASS")


if __name__ == "__main__":
    _run_self_test()