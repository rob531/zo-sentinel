"""Logic for the perspective unseen-event backlog service."""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent


def get_unseen_backlog(
    db: Session, perspective_id: int | str, limit: int = 50
) -> dict[str, Any]:
    """Return unseen events for a perspective and the total unseen count."""
    normalized_limit = max(0, int(limit))

    unseen_count = int(
        db.execute(
            select(func.count(PerspectiveEvent.id)).where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.seen.is_(False),
            )
        ).scalar_one()
    )

    rows = db.execute(
        select(
            PerspectiveEvent.id.label("event_id"),
            PerspectiveEvent.server_id,
            McpServerRegistry.name.label("server_name"),
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.created_at,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == PerspectiveEvent.server_id,
        )
        .where(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen.is_(False),
        )
        .order_by(desc(PerspectiveEvent.created_at), desc(PerspectiveEvent.id))
        .limit(normalized_limit)
    ).all()

    backlog = [
        {
            "event_id": row.event_id,
            "server_id": row.server_id,
            "server_name": row.server_name,
            "change_type": row.change_type,
            "old_tier": row.old_tier,
            "new_tier": row.new_tier,
            "created_at": row.created_at,
        }
        for row in rows
    ]

    return {
        "perspective_id": perspective_id,
        "unseen_count": unseen_count,
        "backlog": backlog,
    }


def list_unseen_events(
    db: Session, perspective_id: int | str, limit: int = 50
) -> list[dict[str, Any]]:
    """Return the backlog rows for a perspective."""
    return get_unseen_backlog(db, perspective_id, limit)["backlog"]


def count_unseen_events(db: Session, perspective_id: int | str) -> int:
    """Count unseen events for a perspective."""
    return int(
        db.execute(
            select(func.count(PerspectiveEvent.id)).where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.seen.is_(False),
            )
        ).scalar_one()
    )


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-a",
                    name="Server A",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="srv-b",
                    name="Server B",
                    risk_tier="low",
                ),
                PerspectiveEvent(
                    id=1,
                    perspective_id=1,
                    server_id="srv-a",
                    change_type="tier_changed",
                    old_tier="medium",
                    new_tier="high",
                    seen=False,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id=1,
                    server_id="srv-b",
                    change_type="tier_changed",
                    old_tier="low",
                    new_tier="medium",
                    seen=False,
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id=2,
                    server_id="srv-a",
                    change_type="tier_changed",
                    old_tier="medium",
                    new_tier="low",
                    seen=True,
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/perspectives/unseen-backlog")
    def unseen_backlog(
        perspective_id: int,
        limit: int = 50,
        db: Session = Depends(get_session),
    ):
        return get_unseen_backlog(db, perspective_id, limit)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/perspectives/unseen-backlog",
        params={"perspective_id": 1, "limit": 50},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["unseen_count"] == 2
    assert len(payload["backlog"]) == 2
    assert any(
        row["server_id"] == "srv-a"
        and row["old_tier"] == "medium"
        and row["new_tier"] == "high"
        for row in payload["backlog"]
    )
    print("PASS")