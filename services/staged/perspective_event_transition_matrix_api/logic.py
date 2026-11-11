from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import asc, case, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

router = APIRouter(prefix="/api")


class PerspectiveTransition(BaseModel):
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    count: int
    servers: int
    unseen_count: int
    last_seen: Optional[str] = None


class PerspectiveTransitionMatrix(BaseModel):
    total_events: int
    transitions: List[PerspectiveTransition]


def _iso_timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def calculate_transition_matrix(
    session: Session,
    perspective_id: Optional[int] = None,
    days: Optional[int] = None,
) -> PerspectiveTransitionMatrix:
    filters = []
    if perspective_id is not None:
        filters.append(PerspectiveEvent.perspective_id == perspective_id)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filters.append(PerspectiveEvent.created_at >= cutoff)

    total_events = (
        session.execute(
            select(func.count())
            .select_from(PerspectiveEvent)
            .where(*filters)
        ).scalar()
        or 0
    )

    transition_count = func.count(PerspectiveEvent.id)
    distinct_servers = func.count(func.distinct(PerspectiveEvent.server_id))
    unseen_count = func.sum(
        case((PerspectiveEvent.seen == False, 1), else_=0)  # noqa: E712
    )
    last_seen = func.max(PerspectiveEvent.created_at)

    statement = (
        select(
            PerspectiveEvent.old_tier.label("old_tier"),
            PerspectiveEvent.new_tier.label("new_tier"),
            transition_count.label("transition_count"),
            distinct_servers.label("server_count"),
            unseen_count.label("unseen_count"),
            last_seen.label("last_seen"),
        )
        .where(*filters)
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .order_by(
            desc("transition_count"),
            asc("old_tier"),
            asc("new_tier"),
        )
    )

    transitions = [
        PerspectiveTransition(
            old_tier=row.old_tier,
            new_tier=row.new_tier,
            count=int(row.transition_count),
            servers=int(row.server_count),
            unseen_count=int(row.unseen_count or 0),
            last_seen=_iso_timestamp(row.last_seen),
        )
        for row in session.execute(statement)
    ]

    return PerspectiveTransitionMatrix(
        total_events=int(total_events),
        transitions=transitions,
    )


@router.get(
    "/perspectives/transitions",
    response_model=PerspectiveTransitionMatrix,
)
def get_transition_matrix(
    perspective_id: Optional[int] = Query(default=None),
    days: Optional[int] = Query(default=None, ge=1),
    session: Session = Depends(get_session),
) -> PerspectiveTransitionMatrix:
    return calculate_transition_matrix(
        session=session,
        perspective_id=perspective_id,
        days=days,
    )


get_perspective_transitions = get_transition_matrix


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE perspective_events (
                id INTEGER PRIMARY KEY,
                perspective_id INTEGER NOT NULL,
                server_id INTEGER NOT NULL,
                change_type VARCHAR NOT NULL,
                old_tier VARCHAR,
                new_tier VARCHAR,
                seen BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO perspective_events
                (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
            VALUES
                (1, 10, 100, 'tier_change', 'CRITICAL', 'HIGH_RISK_ISOLATED', 1, '2024-01-01 00:00:00'),
                (2, 10, 101, 'tier_change', 'CRITICAL', 'HIGH_RISK_ISOLATED', 0, '2024-01-02 00:00:00'),
                (3, 10, 102, 'tier_change', 'HIGH_RISK_ISOLATED', 'TRUSTED_GENERAL', 1, '2024-01-03 00:00:00'),
                (4, 10, 103, 'tier_change', NULL, 'CAUTION_LIMITED', 1, '2024-01-04 00:00:00')
            """
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/transitions")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_events"] == 4
        assert payload["transitions"][0]["count"] == 2
        assert payload["transitions"][0]["unseen_count"] == 1

    print("PASS")