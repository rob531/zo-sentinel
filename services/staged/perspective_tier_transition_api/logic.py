from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


def _build_tier_transition_data(session: Session) -> Dict[str, Any]:
    transition_rows = session.execute(
        text(
            """
            SELECT
                old_tier,
                new_tier,
                COUNT(*) AS count,
                COUNT(DISTINCT server_id) AS servers
            FROM perspective_events
            GROUP BY old_tier, new_tier
            ORDER BY old_tier, new_tier
            """
        )
    ).all()

    change_type_rows = session.execute(
        text(
            """
            SELECT change_type, COUNT(*) AS count
            FROM perspective_events
            GROUP BY change_type
            ORDER BY change_type
            """
        )
    ).all()

    unseen_count = session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM perspective_events
            WHERE seen = :seen
            """
        ),
        {"seen": False},
    ).scalar_one()

    total_events = session.execute(
        text("SELECT COUNT(*) FROM perspective_events")
    ).scalar_one()

    transitions: List[Dict[str, Any]] = [
        {
            "old_tier": row._mapping["old_tier"],
            "new_tier": row._mapping["new_tier"],
            "count": int(row._mapping["count"]),
            "servers": int(row._mapping["servers"]),
        }
        for row in transition_rows
    ]

    change_types: List[Dict[str, Any]] = [
        {
            "change_type": row._mapping["change_type"],
            "count": int(row._mapping["count"]),
        }
        for row in change_type_rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transitions": transitions,
        "change_types": change_types,
        "unseen_count": int(unseen_count),
        "total_events": int(total_events),
    }


def get_transition_data(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _build_tier_transition_data(session)


get_tier_transitions = get_transition_data


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
    TestingSessionLocal = sessionmaker(bind=test_engine)

    test_app = FastAPI()

    @test_app.get("/api/perspectives/tier-transitions")
    def tier_transitions_endpoint(payload: Dict[str, Any] = Depends(get_transition_data)):
        return payload

    with TestingSessionLocal() as test_session:
        test_session.execute(
            text(
                """
                INSERT INTO perspective_events
                    (id, perspective_id, server_id, change_type, old_tier, new_tier, seen, created_at)
                VALUES
                    (:id, :perspective_id, :server_id, :change_type, :old_tier, :new_tier, :seen, :created_at)
                """
            ),
            [
                {
                    "id": 1,
                    "perspective_id": 1,
                    "server_id": 1,
                    "change_type": "added",
                    "old_tier": "standard",
                    "new_tier": "elevated",
                    "seen": True,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": 2,
                    "perspective_id": 1,
                    "server_id": 2,
                    "change_type": "added",
                    "old_tier": "standard",
                    "new_tier": "elevated",
                    "seen": False,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": 3,
                    "perspective_id": 1,
                    "server_id": 1,
                    "change_type": "changed",
                    "old_tier": "elevated",
                    "new_tier": "high",
                    "seen": True,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": 4,
                    "perspective_id": 1,
                    "server_id": 2,
                    "change_type": "changed",
                    "old_tier": "elevated",
                    "new_tier": "high",
                    "seen": True,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": 5,
                    "perspective_id": 1,
                    "server_id": 3,
                    "change_type": "changed",
                    "old_tier": "elevated",
                    "new_tier": "high",
                    "seen": False,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": 6,
                    "perspective_id": 1,
                    "server_id": 1,
                    "change_type": "removed",
                    "old_tier": "high",
                    "new_tier": "standard",
                    "seen": True,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": 7,
                    "perspective_id": 1,
                    "server_id": 2,
                    "change_type": "removed",
                    "old_tier": "high",
                    "new_tier": "standard",
                    "seen": True,
                    "created_at": datetime.now(timezone.utc),
                },
            ],
        )
        test_session.commit()

        test_app.dependency_overrides[get_session] = lambda: test_session
        response = TestClient(test_app).get("/api/perspectives/tier-transitions")

        assert response.status_code == 200
        payload = response.json()
        assert payload["total_events"] == 7
        assert payload["unseen_count"] == 2
        assert any(
            transition["old_tier"] == "standard"
            and transition["new_tier"] == "elevated"
            and transition["count"] == 2
            for transition in payload["transitions"]
        )

    print("PASS")