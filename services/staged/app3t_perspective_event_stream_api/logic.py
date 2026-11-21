from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


router = APIRouter(prefix="/api")


def _iso8601(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_perspective_events(
    db: Session,
    perspective_id: str,
    limit: int = 50,
    since: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return the read-only perspective change stream for one perspective."""
    perspective = db.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()
    if perspective is None:
        raise HTTPException(status_code=404, detail="unknown_perspective")

    statement = select(PerspectiveEvent).where(
        PerspectiveEvent.perspective_id == perspective_id
    )
    if since is not None:
        statement = statement.where(PerspectiveEvent.created_at >= since)

    statement = statement.order_by(
        PerspectiveEvent.created_at.desc(), PerspectiveEvent.id.desc()
    ).limit(limit)
    rows = db.execute(statement).scalars().all()

    events: List[Dict[str, Any]] = [
        {
            "id": int(event.id),
            "server_id": None if event.server_id is None else str(event.server_id),
            "change_type": str(event.change_type),
            "old_tier": event.old_tier,
            "new_tier": event.new_tier,
            "seen": bool(event.seen),
            "created_at": _iso8601(event.created_at),
        }
        for event in rows
    ]

    return {
        "perspective_id": str(perspective.id),
        "perspective_name": perspective.name,
        "count": len(events),
        "events": events,
    }


@router.get("/perspectives/{perspective_id}/events")
def perspective_events_endpoint(
    perspective_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    since: Optional[datetime] = None,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_perspective_events(
        db=db,
        perspective_id=perspective_id,
        limit=limit,
        since=since,
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    Perspective.__table__.create(engine, checkfirst=True)
    PerspectiveEvent.__table__.create(engine, checkfirst=True)

    now = datetime.now()
    with session_factory() as db:
        db.add(
            Perspective(
                id="perspective-test",
                name="Seeded Perspective",
                created_by="test-user",
                facet_filters={},
            )
        )
        db.add_all(
            [
                PerspectiveEvent(
                    perspective_id="perspective-test",
                    server_id="server-1",
                    change_type="tier_change",
                    old_tier="T1",
                    new_tier="T2",
                    seen=True,
                    created_at=now,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-test",
                    server_id="server-2",
                    change_type="new_member",
                    old_tier=None,
                    new_tier="T2",
                    seen=False,
                    created_at=now,
                ),
                PerspectiveEvent(
                    perspective_id="perspective-test",
                    server_id="server-3",
                    change_type="removed_member",
                    old_tier="T2",
                    new_tier=None,
                    seen=True,
                    created_at=now,
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Session:
        with session_factory() as db:
            yield db

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/perspectives/perspective-test/events")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert len(payload["events"]) == 3
    tier_changes = [
        event
        for event in payload["events"]
        if event["change_type"] == "tier_change"
    ]
    assert len(tier_changes) == 1
    assert tier_changes[0]["old_tier"] is not None
    assert tier_changes[0]["new_tier"] is not None
    assert payload["perspective_name"] == "Seeded Perspective"

    unknown = client.get("/api/perspectives/unknown-perspective/events")
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "unknown_perspective"}

    client.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()