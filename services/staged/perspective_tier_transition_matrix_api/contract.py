from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


class TierTransition(BaseModel):
    old_tier: str
    new_tier: str
    count: int


class TierTransitionResponse(BaseModel):
    perspective_id: Optional[int]
    days: int
    transitions: List[TierTransition]
    unseen_count: int
    servers_affected: int
    total_events: int


router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/tier-transitions",
    response_model=TierTransitionResponse,
)
def get_tier_transition_matrix(
    perspective_id: Optional[int] = Query(default=None),
    days: int = Query(default=30, ge=1),
    session: Session = Depends(get_session),
) -> TierTransitionResponse:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    filters = [
        PerspectiveEvent.created_at >= cutoff,
        PerspectiveEvent.created_at <= now,
    ]
    if perspective_id is not None:
        filters.append(PerspectiveEvent.perspective_id == perspective_id)

    transition_rows = session.execute(
        select(
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            func.count(PerspectiveEvent.id).label("transition_count"),
        )
        .where(
            *filters,
            PerspectiveEvent.old_tier.is_not(None),
            PerspectiveEvent.new_tier.is_not(None),
        )
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .order_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
    ).all()

    transitions = [
        TierTransition(
            old_tier=old_tier,
            new_tier=new_tier,
            count=count,
        )
        for old_tier, new_tier, count in transition_rows
    ]

    unseen_count = session.execute(
        select(func.count(PerspectiveEvent.id))
        .where(*filters, PerspectiveEvent.seen == False)  # noqa: E712
    ).scalar_one()

    servers_affected = session.execute(
        select(func.count(func.distinct(PerspectiveEvent.server_id)))
        .where(*filters)
    ).scalar_one()

    total_events = session.execute(
        select(func.count(PerspectiveEvent.id)).where(*filters)
    ).scalar_one()

    return TierTransitionResponse(
        perspective_id=perspective_id,
        days=days,
        transitions=transitions,
        unseen_count=unseen_count,
        servers_affected=servers_affected,
        total_events=total_events,
    )


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine, autoflush=False)

    def override_get_session():
        test_session = test_session_factory()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    now = datetime.now(timezone.utc)
    with test_session_factory() as test_session:
        test_session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id=101,
                    server_id="server-a",
                    change_type="tier_change",
                    old_tier="low",
                    new_tier="medium",
                    seen=True,
                    created_at=now,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id=101,
                    server_id="server-b",
                    change_type="tier_change",
                    old_tier="medium",
                    new_tier="high",
                    seen=False,
                    created_at=now,
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id=202,
                    server_id="server-c",
                    change_type="tier_change",
                    old_tier="high",
                    new_tier="low",
                    seen=True,
                    created_at=now,
                ),
            ]
        )
        test_session.commit()

    with TestClient(that_app) as client:
        response = client.get(
            "/api/perspectives/tier-transitions",
            params={"perspective_id": 101, "days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["perspective_id"] == 101
        assert payload["days"] == 30
        assert payload["unseen_count"] == 1
        assert payload["servers_affected"] == 2
        assert payload["total_events"] == 2
        assert any(transition["count"] == 1 for transition in payload["transitions"])

        all_response = client.get(
            "/api/perspectives/tier-transitions",
            params={"days": 30},
        )
        assert all_response.status_code == 200, all_response.text
        assert all_response.json()["perspective_id"] is None
        assert all_response.json()["total_events"] == 3

    test_engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()
    raise SystemExit(0)