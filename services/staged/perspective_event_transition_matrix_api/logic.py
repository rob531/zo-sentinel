from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


class PerspectiveTransition(BaseModel):
    old_tier: Optional[str]
    new_tier: Optional[str]
    count: int
    servers: int
    unseen_count: int
    last_seen: Optional[str]


class PerspectiveTransitionMatrix(BaseModel):
    total_events: int
    transitions: List[PerspectiveTransition]


def get_transition_matrix(
    session: Session,
    perspective_id: Optional[str] = None,
    days: Optional[int] = None,
) -> PerspectiveTransitionMatrix:
    statement = select(
        PerspectiveEvent.old_tier.label("old_tier"),
        PerspectiveEvent.new_tier.label("new_tier"),
        func.count().label("event_count"),
        func.count(func.distinct(PerspectiveEvent.server_id)).label("server_count"),
        func.sum(
            case((PerspectiveEvent.seen.is_(False), 1), else_=0)
        ).label("unseen_count"),
        func.max(PerspectiveEvent.created_at).label("last_seen"),
    )

    if perspective_id is not None:
        statement = statement.where(
            PerspectiveEvent.perspective_id == perspective_id
        )

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        statement = statement.where(PerspectiveEvent.created_at >= cutoff)

    statement = (
        statement.group_by(
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
        )
        .order_by(
            func.count().desc(),
            PerspectiveEvent.old_tier.asc(),
            PerspectiveEvent.new_tier.asc(),
        )
    )

    rows = session.execute(statement).all()
    transitions = []

    for row in rows:
        last_seen = row.last_seen
        if last_seen is not None and hasattr(last_seen, "isoformat"):
            last_seen = last_seen.isoformat()
        elif last_seen is not None:
            last_seen = str(last_seen)

        transitions.append(
            PerspectiveTransition(
                old_tier=row.old_tier,
                new_tier=row.new_tier,
                count=int(row.event_count),
                servers=int(row.server_count),
                unseen_count=int(row.unseen_count or 0),
                last_seen=last_seen,
            )
        )

    return PerspectiveTransitionMatrix(
        total_events=sum(transition.count for transition in transitions),
        transitions=transitions,
    )


get_transitions = get_transition_matrix
get_perspective_transitions = get_transition_matrix
get_perspective_transition_matrix = get_transition_matrix
compute_transition_matrix = get_transition_matrix


router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/transitions",
    response_model=PerspectiveTransitionMatrix,
)
def read_perspective_transition_matrix(
    perspective_id: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> PerspectiveTransitionMatrix:
    return get_transition_matrix(
        session=session,
        perspective_id=perspective_id,
        days=days,
    )


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
    PerspectiveEvent.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as test_session:
        now = datetime.now(timezone.utc)
        test_session.add_all(
            [
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="RISK_TIER_CHANGED",
                    old_tier="CRITICAL",
                    new_tier="HIGH_RISK_ISOLATED",
                    seen=True,
                    created_at=now,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-2",
                    change_type="RISK_TIER_CHANGED",
                    old_tier="CRITICAL",
                    new_tier="HIGH_RISK_ISOLATED",
                    seen=False,
                    created_at=now,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-3",
                    change_type="RISK_TIER_CHANGED",
                    old_tier="HIGH_RISK_ISOLATED",
                    new_tier="TRUSTED_GENERAL",
                    seen=True,
                    created_at=now,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-1",
                    server_id="server-4",
                    change_type="RISK_TIER_CHANGED",
                    old_tier=None,
                    new_tier="CAUTION_LIMITED",
                    seen=True,
                    created_at=now,
                ),
            ]
        )
        test_session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        override_session = TestSession()
        try:
            yield override_session
        finally:
            override_session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/transitions")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_events"] == 4, payload
        assert payload["transitions"], payload
        assert payload["transitions"][0]["count"] == 2, payload
        assert payload["transitions"][0]["unseen_count"] == 1, payload

    print("PASS")