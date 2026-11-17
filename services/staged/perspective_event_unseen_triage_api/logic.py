from datetime import datetime
from typing import Dict, List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, desc, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


class PerspectiveEventItem(BaseModel):
    id: int
    perspective_id: int
    server_id: Optional[int]
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    created_at: datetime


class UnseenEventsResponse(BaseModel):
    total_unseen: int
    by_change_type: Dict[str, int]
    events: List[PerspectiveEventItem]


UnseenEventResponse = PerspectiveEventItem
PerspectiveEventResponse = PerspectiveEventItem
UnseenEvents = UnseenEventsResponse


def get_unseen_events(
    session: Session = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> UnseenEventsResponse:
    unseen = or_(
        PerspectiveEvent.seen == bindparam("unseen_false", False),
        PerspectiveEvent.seen.is_(None),
    )

    total_unseen = int(
        session.execute(
            select(func.count())
            .select_from(PerspectiveEvent)
            .where(unseen)
        ).scalar_one()
    )

    count_rows = session.execute(
        select(PerspectiveEvent.change_type, func.count())
        .where(unseen)
        .group_by(PerspectiveEvent.change_type)
    ).all()
    by_change_type = {
        str(change_type): int(count)
        for change_type, count in count_rows
        if change_type is not None
    }

    rows = session.execute(
        select(PerspectiveEvent)
        .where(unseen)
        .order_by(desc(PerspectiveEvent.created_at), desc(PerspectiveEvent.id))
        .limit(limit)
    ).scalars().all()

    events = [
        PerspectiveEventItem(
            id=row.id,
            perspective_id=row.perspective_id,
            server_id=row.server_id,
            change_type=row.change_type,
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            created_at=row.created_at,
        )
        for row in rows
    ]

    return UnseenEventsResponse(
        total_unseen=total_unseen,
        by_change_type=by_change_type,
        events=events,
    )


if __name__ == "__main__":
    from datetime import timedelta

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
    PerspectiveEvent.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    base_time = datetime(2025, 1, 1, 12, 0, 0)
    with TestSession() as db:
        db.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id=101,
                    server_id=201,
                    change_type="tier_up",
                    old_tier="low",
                    new_tier="medium",
                    seen=False,
                    created_at=base_time,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id=102,
                    server_id=202,
                    change_type="tier_down",
                    old_tier="high",
                    new_tier="medium",
                    seen=False,
                    created_at=base_time + timedelta(minutes=2),
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id=103,
                    server_id=203,
                    change_type="metadata",
                    old_tier=None,
                    new_tier=None,
                    seen=True,
                    created_at=base_time + timedelta(minutes=3),
                ),
                PerspectiveEvent(
                    id=4,
                    perspective_id=104,
                    server_id=204,
                    change_type="tier_up",
                    old_tier="medium",
                    new_tier="high",
                    seen=None,
                    created_at=base_time + timedelta(minutes=1),
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()

    @test_app.get("/api/perspectives/events/unseen", response_model=UnseenEventsResponse)
    def unseen_events(
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_session),
    ):
        return get_unseen_events(session=session, limit=limit)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/events/unseen")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_unseen"] == 3
        assert sum(payload["by_change_type"].values()) == 3
        created_at = [event["created_at"] for event in payload["events"]]
        assert created_at == sorted(created_at, reverse=True)
        assert len(payload["events"]) == 3
        assert all("seen" not in event for event in payload["events"])

    print("PASS")