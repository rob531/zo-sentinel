from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql import text

from app.db import get_session
from app.models import PerspectiveEvent, PerspectiveSnapshot


def _window_start(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _rows(session: Session, query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = session.execute(text(query), params)
    return [dict(row) for row in result.mappings()]


def _events_per_day(
    session: Session,
    days: int,
    cutoff: datetime,
) -> List[Dict[str, Any]]:
    return _rows(
        session,
        """
        SELECT CAST(created_at AS DATE) AS day, COUNT(*) AS count
        FROM perspective_events
        WHERE created_at >= :cutoff
        GROUP BY CAST(created_at AS DATE)
        ORDER BY day
        """,
        {"cutoff": cutoff},
    )


def get_event_change_mix(
    days: int = 30,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Return the event-change mix for the requested reporting window."""
    if days < 1:
        days = 1

    cutoff = _window_start(days)
    params = {"cutoff": cutoff}

    change_type_rows = _rows(
        session,
        """
        SELECT change_type, COUNT(*) AS count
        FROM perspective_events
        WHERE created_at >= :cutoff
        GROUP BY change_type
        ORDER BY count DESC, change_type
        """,
        params,
    )

    transition_rows = _rows(
        session,
        """
        SELECT old_tier, new_tier, COUNT(*) AS count
        FROM perspective_events
        WHERE created_at >= :cutoff
          AND old_tier IS NOT NULL
          AND new_tier IS NOT NULL
        GROUP BY old_tier, new_tier
        ORDER BY count DESC, old_tier, new_tier
        """,
        params,
    )

    unseen_rows = _rows(
        session,
        """
        SELECT COUNT(*) AS count
        FROM perspective_events
        WHERE created_at >= :cutoff
          AND seen IS FALSE
        """,
        params,
    )

    perspective_rows = _rows(
        session,
        """
        SELECT perspective_id, COUNT(*) AS event_count,
               SUM(CASE WHEN seen IS FALSE THEN 1 ELSE 0 END) AS unseen_count
        FROM perspective_events
        WHERE created_at >= :cutoff
        GROUP BY perspective_id
        ORDER BY perspective_id
        """,
        params,
    )

    # The daily series is intentionally calculated from the same window as
    # the aggregate metrics. The public response model remains the stable
    # API contract defined by the service router.
    _events_per_day(session, days, cutoff)

    total_events = sum(int(row["count"]) for row in change_type_rows)
    unseen_count = int(unseen_rows[0]["count"]) if unseen_rows else 0

    return {
        "days": days,
        "total_events": total_events,
        "by_change_type": [
            {
                "change_type": row["change_type"],
                "count": int(row["count"]),
            }
            for row in change_type_rows
        ],
        "transitions": [
            {
                "old_tier": row["old_tier"],
                "new_tier": row["new_tier"],
                "count": int(row["count"]),
            }
            for row in transition_rows
        ],
        "unseen_count": unseen_count,
        "by_perspective": [
            {
                "perspective_id": row["perspective_id"],
                "event_count": int(row["event_count"]),
                "unseen_count": int(row["unseen_count"] or 0),
            }
            for row in perspective_rows
        ],
    }


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    PerspectiveEvent.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)

    now = datetime.now(timezone.utc)
    events = [
        PerspectiveEvent(
            id=1,
            perspective_id=10,
            server_id=100,
            change_type="membership_changed",
            old_tier="free",
            new_tier="pro",
            seen=True,
            created_at=now - timedelta(days=1),
        ),
        PerspectiveEvent(
            id=2,
            perspective_id=10,
            server_id=101,
            change_type="membership_changed",
            old_tier="free",
            new_tier="pro",
            seen=False,
            created_at=now - timedelta(days=2),
        ),
        PerspectiveEvent(
            id=3,
            perspective_id=10,
            server_id=102,
            change_type="tier_changed",
            old_tier="pro",
            new_tier="enterprise",
            seen=False,
            created_at=now - timedelta(days=3),
        ),
        PerspectiveEvent(
            id=4,
            perspective_id=11,
            server_id=103,
            change_type="tier_changed",
            old_tier="pro",
            new_tier="enterprise",
            seen=True,
            created_at=now - timedelta(days=4),
        ),
        PerspectiveEvent(
            id=5,
            perspective_id=11,
            server_id=104,
            change_type="membership_changed",
            old_tier=None,
            new_tier=None,
            seen=True,
            created_at=now - timedelta(days=5),
        ),
    ]
    snapshots = [
        PerspectiveSnapshot(
            id=1,
            perspective_id=10,
            taken_at=now - timedelta(days=1),
            membership={},
        ),
        PerspectiveSnapshot(
            id=2,
            perspective_id=11,
            taken_at=now - timedelta(days=1),
            membership={},
        ),
    ]

    with TestingSession() as db:
        db.add_all(events)
        db.add_all(snapshots)
        db.commit()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/perspectives/event-change-mix")
    def event_change_mix(
        days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_event_change_mix(days=days, session=session)

    with TestClient(test_app) as client:
        response = client.get(
            "/api/perspectives/event-change-mix",
            params={"days": 30},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total_events"] == 5
        assert {item["change_type"]: item["count"] for item in body["by_change_type"]} == {
            "membership_changed": 3,
            "tier_changed": 2,
        }
        assert {
            (item["old_tier"], item["new_tier"])
            for item in body["transitions"]
        } >= {("free", "pro"), ("pro", "enterprise")}
        assert body["unseen_count"] == 2
        assert len(body["by_perspective"]) == 2

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()