"""logic.py -- the service's data/computation layer (one concern, one file).

Reads the real perspective event data through the application database session.
"""
from __future__ import annotations

from typing import Dict, List

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveEvent


def get_event_seen_mix(db: Session) -> Dict[str, object]:
    """Aggregate perspective events by change type and server count."""
    seen_expression = func.sum(
        case((PerspectiveEvent.seen.is_(True), 1), else_=0)
    )

    rows = db.execute(
        select(
            PerspectiveEvent.change_type,
            func.count(PerspectiveEvent.id),
            seen_expression,
            func.count(func.distinct(PerspectiveEvent.server_id)),
        )
        .group_by(PerspectiveEvent.change_type)
        .order_by(PerspectiveEvent.change_type)
    ).all()

    change_types: List[Dict[str, object]] = []
    total_events = 0
    total_unseen = 0

    for change_type, events, seen, servers in rows:
        events = int(events or 0)
        seen = int(seen or 0)
        unseen = events - seen
        seen_pct = round((seen / events) * 100, 1) if events else 0.0

        change_types.append(
            {
                "change_type": str(change_type),
                "events": events,
                "seen": seen,
                "unseen": unseen,
                "seen_pct": seen_pct,
                "servers": int(servers or 0),
            }
        )
        total_events += events
        total_unseen += unseen

    return {
        "change_types": change_types,
        "totals": {"events": total_events, "unseen": total_unseen},
    }


# Keep the common computation names available to thin routers.
aggregate_event_seen_mix = get_event_seen_mix
event_seen_mix = get_event_seen_mix
compute_event_seen_mix = get_event_seen_mix
get_seen_mix = get_event_seen_mix


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PerspectiveEvent.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    that_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/perspectives/event-seen-mix")
    def event_seen_mix_endpoint(db: Session = Depends(get_session)):
        return get_event_seen_mix(db)

    with SessionLocal() as session:
        session.add_all(
            [
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="tier_change",
                    seen=True,
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-1",
                    server_id="server-2",
                    change_type="tier_change",
                    seen=True,
                ),
                PerspectiveEvent(
                    id=3,
                    perspective_id="perspective-1",
                    server_id="server-1",
                    change_type="added",
                    seen=False,
                ),
            ]
        )
        session.commit()

        result = get_event_seen_mix(session)
        by_type = {
            row["change_type"]: row for row in result["change_types"]
        }
        assert len(by_type) == 2
        assert by_type["tier_change"]["events"] == 2
        assert by_type["tier_change"]["seen"] == 2
        assert by_type["tier_change"]["seen_pct"] == 100.0
        assert by_type["added"]["unseen"] == 1
        assert result["totals"]["events"] == 3

    print("PASS")