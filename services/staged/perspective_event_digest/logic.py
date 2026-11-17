"""Perspective event digest logic over the real application data layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


class Transition(BaseModel):
    perspective_id: int
    server_id: int
    old_tier: str | None = None
    new_tier: str | None = None
    seen: bool
    created_at: datetime


class PerspectiveRollup(BaseModel):
    perspective_id: int
    name: str | None = None
    event_count: int
    transition_count: int
    unseen_count: int
    server_count: int


class DigestResponse(BaseModel):
    hours: int
    perspective_count: int
    server_count: int
    transitions: List[Transition]
    unseen_count: int
    perspective_rollups: List[PerspectiveRollup]


router = APIRouter(prefix="/api/perspectives/events")


def _digest(db: Session, hours: int) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(PerspectiveEvent, Perspective.name)
        .outerjoin(Perspective, Perspective.id == PerspectiveEvent.perspective_id)
        .where(PerspectiveEvent.created_at >= cutoff)
        .order_by(PerspectiveEvent.created_at.asc(), PerspectiveEvent.id.asc())
    ).all()

    transitions: List[Dict[str, Any]] = []
    rollups: Dict[int, Dict[str, Any]] = {}
    servers = set()

    for event, perspective_name in rows:
        transitions.append(
            {
                "perspective_id": event.perspective_id,
                "server_id": event.server_id,
                "old_tier": event.old_tier,
                "new_tier": event.new_tier,
                "seen": bool(event.seen),
                "created_at": event.created_at,
            }
        )
        servers.add(event.server_id)

        rollup = rollups.setdefault(
            event.perspective_id,
            {
                "perspective_id": event.perspective_id,
                "name": perspective_name,
                "event_count": 0,
                "transition_count": 0,
                "unseen_count": 0,
                "server_count": 0,
                "_servers": set(),
            },
        )
        rollup["event_count"] += 1
        rollup["_servers"].add(event.server_id)
        if not event.seen:
            rollup["unseen_count"] += 1
        if event.old_tier != event.new_tier:
            rollup["transition_count"] += 1

    perspective_rollups: List[Dict[str, Any]] = []
    for rollup in rollups.values():
        rollup["server_count"] = len(rollup.pop("_servers"))
        perspective_rollups.append(rollup)

    return {
        "hours": hours,
        "perspective_count": len(rollups),
        "server_count": len(servers),
        "transitions": transitions,
        "unseen_count": sum(1 for item in transitions if not item["seen"]),
        "perspective_rollups": perspective_rollups,
    }


@router.get("/digest", response_model=DigestResponse)
def digest(
    hours: int = 168,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _digest(db, hours)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    import app.models as models

    models.Base.metadata.create_all(engine)
    db = TestingSession()
    db.add(
        models.Perspective(
            id=1,
            created_by=1,
            name="test perspective",
            facet_filters={},
        )
    )
    db.commit()

    db.add_all(
        [
            models.PerspectiveEvent(
                id=1,
                perspective_id=1,
                server_id=101,
                change_type="membership",
                old_tier=None,
                new_tier="high",
                seen=False,
            ),
            models.PerspectiveEvent(
                id=2,
                perspective_id=1,
                server_id=102,
                change_type="membership",
                old_tier="medium",
                new_tier="high",
                seen=True,
            ),
            models.PerspectiveEvent(
                id=3,
                perspective_id=1,
                server_id=103,
                change_type="membership",
                old_tier="high",
                new_tier="high",
                seen=True,
            ),
        ]
    )
    db.commit()

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        yield db

    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/perspectives/events/digest")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["transitions"]) == 3, payload
        assert payload["unseen_count"] == 1, payload

    print("PASS")