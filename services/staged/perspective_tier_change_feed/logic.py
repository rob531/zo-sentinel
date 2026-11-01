"""logic.py -- perspective tier-change feed data and computation layer."""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent


DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def _event_dict(
    event: PerspectiveEvent, server_name: str | None
) -> Dict[str, Any]:
    return {
        "server_id": event.server_id,
        "server_name": server_name or "",
        "change_type": event.change_type,
        "old_tier": event.old_tier,
        "new_tier": event.new_tier,
        "seen": bool(event.seen),
        "created_at": event.created_at,
    }


def get_perspective_changes(
    db: Session, perspective_id: int, limit: int = DEFAULT_LIMIT
) -> Dict[str, Any]:
    """Return recent tier changes and their aggregates for one perspective."""
    try:
        normalized_limit = max(0, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        normalized_limit = DEFAULT_LIMIT

    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if perspective is None:
        raise LookupError(f"Perspective {perspective_id} was not found")

    total = int(
        db.execute(
            select(func.count())
            .select_from(PerspectiveEvent)
            .where(PerspectiveEvent.perspective_id == perspective_id)
        ).scalar_one()
    )
    unseen = int(
        db.execute(
            select(func.count())
            .select_from(PerspectiveEvent)
            .where(
                PerspectiveEvent.perspective_id == perspective_id,
                PerspectiveEvent.seen.is_(False),
            )
        ).scalar_one()
    )

    rows = db.execute(
        select(PerspectiveEvent, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == PerspectiveEvent.server_id,
            isouter=True,
        )
        .join(Perspective, Perspective.id == PerspectiveEvent.perspective_id)
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(
            PerspectiveEvent.created_at.desc(),
            PerspectiveEvent.id.desc(),
        )
        .limit(normalized_limit)
    ).all()

    events = [_event_dict(event, registry.name) for event, registry in rows]

    by_change_type: Dict[str, int] = {}
    by_new_tier: Dict[str, int] = {}
    for event in events:
        change_type = str(event["change_type"] or "unknown")
        new_tier = str(event["new_tier"] or "unknown")
        by_change_type[change_type] = by_change_type.get(change_type, 0) + 1
        by_new_tier[new_tier] = by_new_tier.get(new_tier, 0) + 1

    return {
        "perspective_id": perspective.id,
        "perspective_name": perspective.name,
        "total": total,
        "unseen": unseen,
        "events": events,
        "by_change_type": by_change_type,
        "by_new_tier": by_new_tier,
    }


# Keep the common service-layer names available to thin routers.
list_perspective_changes = get_perspective_changes
get_changes = get_perspective_changes


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Perspective.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        session.add(
            Perspective(
                id=1,
                name="Contract Perspective",
                created_by="contract-test",
                description="",
                facet_filters={},
            )
        )
        for index in range(1, 4):
            session.add(
                McpServerRegistry(
                    server_id=f"server-{index}",
                    name=f"Server {index}",
                )
            )

        now = datetime.now(timezone.utc)
        for index in range(1, 5):
            session.add(
                PerspectiveEvent(
                    id=index,
                    perspective_id=1,
                    server_id=f"server-{((index - 1) % 3) + 1}",
                    change_type="tier_change",
                    old_tier="low" if index % 2 else "medium",
                    new_tier="high" if index % 2 else "critical",
                    seen=index == 4,
                    created_at=now - timedelta(minutes=index),
                )
            )
        session.commit()

        application = FastAPI()

        @application.get("/api/perspectives/{perspective_id}/changes")
        def changes(
            perspective_id: int,
            limit: int = DEFAULT_LIMIT,
            db: Session = Depends(get_session),
        ):
            return get_perspective_changes(db, perspective_id, limit)

        def override_get_session():
            yield session

        application.dependency_overrides[get_session] = override_get_session
        with TestClient(application) as client:
            response = client.get(
                "/api/perspectives/1/changes",
                params={"limit": 10},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 4
            assert payload["unseen"] == 3
            assert len(payload["events"]) == 4
            assert payload["events"][0]["created_at"] >= payload["events"][1]["created_at"]
            assert payload["events"][1]["created_at"] >= payload["events"][2]["created_at"]
            assert payload["events"][2]["created_at"] >= payload["events"][3]["created_at"]
            assert payload["by_change_type"] == {"tier_change": 4}
            assert payload["by_new_tier"] == {"high": 2, "critical": 2}

    print("PASS")