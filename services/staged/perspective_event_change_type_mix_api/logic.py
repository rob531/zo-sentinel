from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent

__all__ = ["get_change_type_mix"]


def get_change_type_mix(db: Session, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))

    total_stmt = select(func.count(PerspectiveEvent.id)).select_from(PerspectiveEvent)
    total = int(db.execute(total_stmt).scalar_one())

    count_expression = func.count(PerspectiveEvent.id)
    change_type_stmt = (
        select(
            PerspectiveEvent.change_type.label("change_type"),
            count_expression.label("count"),
            func.sum(
                case((PerspectiveEvent.seen.is_(False), 1), else_=0)
            ).label("unseen"),
            func.sum(
                case((PerspectiveEvent.seen.is_(True), 1), else_=0)
            ).label("seen"),
        )
        .select_from(PerspectiveEvent)
        .group_by(PerspectiveEvent.change_type)
        .order_by(count_expression.desc(), PerspectiveEvent.change_type.asc())
    )
    change_type_rows = db.execute(change_type_stmt).mappings().all()

    transition_count = func.count(PerspectiveEvent.id)
    transition_stmt = (
        select(
            PerspectiveEvent.old_tier.label("old_tier"),
            PerspectiveEvent.new_tier.label("new_tier"),
            transition_count.label("count"),
        )
        .select_from(PerspectiveEvent)
        .group_by(PerspectiveEvent.old_tier, PerspectiveEvent.new_tier)
        .order_by(
            transition_count.desc(),
            PerspectiveEvent.old_tier.asc(),
            PerspectiveEvent.new_tier.asc(),
        )
        .limit(limit)
    )
    transition_rows = db.execute(transition_stmt).mappings().all()

    return {
        "total": total,
        "change_types": [
            {
                "change_type": row["change_type"],
                "count": int(row["count"]),
                "unseen": int(row["unseen"] or 0),
                "seen": int(row["seen"] or 0),
            }
            for row in change_type_rows
        ],
        "top_transitions": [
            {
                "old_tier": row["old_tier"],
                "new_tier": row["new_tier"],
                "count": int(row["count"]),
            }
            for row in transition_rows
        ],
    }


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SQLAlchemySession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(
        bind=engine,
        class_=SQLAlchemySession,
        autoflush=False,
        autocommit=False,
    )

    PerspectiveEvent.__table__.create(engine)
    with TestingSession() as session:
        session.add_all(
            [
                PerspectiveEvent(
                    perspective_id=1,
                    server_id=1,
                    change_type="tier_change",
                    old_tier=None,
                    new_tier="high",
                    seen=False,
                    created_at=datetime.now(timezone.utc),
                ),
                PerspectiveEvent(
                    perspective_id=1,
                    server_id=1,
                    change_type="tier_change",
                    old_tier="high",
                    new_tier="medium",
                    seen=True,
                    created_at=datetime.now(timezone.utc),
                ),
                PerspectiveEvent(
                    perspective_id=1,
                    server_id=1,
                    change_type="membership_add",
                    old_tier=None,
                    new_tier=None,
                    seen=False,
                    created_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/perspectives/change-type-mix")
    def change_type_mix_endpoint(
        limit: int = Query(20, ge=1, le=100),
        session: SQLAlchemySession = Depends(get_session),
    ):
        return get_change_type_mix(session, limit)

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/change-type-mix")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3, payload
        tier_change = next(
            item
            for item in payload["change_types"]
            if item["change_type"] == "tier_change"
        )
        assert tier_change["count"] == 2, payload
        assert tier_change["unseen"] == 1, payload
        assert isinstance(payload["top_transitions"], list), payload
        assert all(
            isinstance(item, dict) and "count" in item
            for item in payload["top_transitions"]
        ), payload

    print("PASS")