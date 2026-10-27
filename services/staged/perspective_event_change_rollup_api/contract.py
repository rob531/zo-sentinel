from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import PerspectiveEvent


def create_app() -> FastAPI:
    api = FastAPI(title="Perspective Event Change Rollup API")

    @api.get("/api/perspectives/event-rollup")
    def event_rollup(
        perspective_id: int | None = Query(default=None),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        filters = []
        if perspective_id is not None:
            filters.append(PerspectiveEvent.perspective_id == perspective_id)

        total_events = session.execute(
            select(func.count()).select_from(PerspectiveEvent).where(*filters)
        ).scalar_one()

        change_type_rows = session.execute(
            select(PerspectiveEvent.change_type, func.count().label("count"))
            .where(*filters)
            .group_by(PerspectiveEvent.change_type)
            .order_by(PerspectiveEvent.change_type)
        ).mappings().all()

        transition_rows = session.execute(
            select(
                func.coalesce(PerspectiveEvent.old_tier, "none").label("old_tier"),
                func.coalesce(PerspectiveEvent.new_tier, "none").label("new_tier"),
                func.count().label("count"),
            )
            .where(*filters)
            .group_by(
                func.coalesce(PerspectiveEvent.old_tier, "none"),
                func.coalesce(PerspectiveEvent.new_tier, "none"),
            )
            .order_by(
                func.coalesce(PerspectiveEvent.old_tier, "none"),
                func.coalesce(PerspectiveEvent.new_tier, "none"),
            )
        ).mappings().all()

        perspective_rows = session.execute(
            select(PerspectiveEvent.perspective_id, func.count().label("count"))
            .where(*filters)
            .group_by(PerspectiveEvent.perspective_id)
            .order_by(PerspectiveEvent.perspective_id)
        ).mappings().all()

        recent_rows = session.execute(
            select(
                PerspectiveEvent.id,
                PerspectiveEvent.perspective_id,
                PerspectiveEvent.server_id,
                PerspectiveEvent.change_type,
                PerspectiveEvent.old_tier,
                PerspectiveEvent.new_tier,
                PerspectiveEvent.seen,
                PerspectiveEvent.created_at,
            )
            .where(*filters)
            .order_by(PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc())
            .limit(10)
        ).mappings().all()

        recent = [dict(row) for row in recent_rows]
        for row in recent:
            if row["old_tier"] is None:
                row["old_tier"] = "none"
            if row["new_tier"] is None:
                row["new_tier"] = "none"

        return {
            "total_events": total_events,
            "by_change_type": [dict(row) for row in change_type_rows],
            "by_transition": [dict(row) for row in transition_rows],
            "by_perspective": [dict(row) for row in perspective_rows],
            "recent": recent,
        }

    return api


app = create_app()


def _seed_and_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(bind=engine)
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": 1,
            "perspective_id": 10,
            "server_id": 100,
            "change_type": "tier_change",
            "old_tier": None,
            "new_tier": "low",
            "seen": True,
            "created_at": now,
        },
        {
            "id": 2,
            "perspective_id": 10,
            "server_id": 101,
            "change_type": "tier_change",
            "old_tier": "low",
            "new_tier": "high",
            "seen": False,
            "created_at": now,
        },
        {
            "id": 3,
            "perspective_id": 11,
            "server_id": 100,
            "change_type": "metadata_change",
            "old_tier": "high",
            "new_tier": None,
            "seen": True,
            "created_at": now,
        },
        {
            "id": 4,
            "perspective_id": 11,
            "server_id": 101,
            "change_type": "metadata_change",
            "old_tier": "high",
            "new_tier": None,
            "seen": False,
            "created_at": now,
        },
    ]

    with test_session_factory() as session:
        session.execute(insert(PerspectiveEvent), rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/perspectives/event-rollup")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_events"] == 4, payload
            assert any(
                row["change_type"] == "tier_change" and row["count"] == 2
                for row in payload["by_change_type"]
            ), payload
            assert any(
                row["old_tier"] == "none"
                for row in payload["by_transition"]
            ), payload
    finally:
        that_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _seed_and_test()