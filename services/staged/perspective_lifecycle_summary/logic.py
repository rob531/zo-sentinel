import datetime
from typing import Dict, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    Base,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
)


class PerspectiveLifecycleSummary(BaseModel):
    perspective_id: int
    event_count: int
    last_snapshot_at: Optional[datetime.datetime]
    tier_changes: Dict[str, int]


def get_perspective_lifecycle_summary(
    perspective_id: int, session: Session = Depends(get_session)
) -> PerspectiveLifecycleSummary:
    # total number of events for this perspective
    event_count = (
        session.query(func.count(PerspectiveEvent.id))
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .scalar()
    )

    # most recent snapshot timestamp
    last_snapshot_at = (
        session.query(func.max(PerspectiveSnapshot.taken_at))
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .scalar()
    )

    # distribution of change_type values (including tier changes)
    change_type_counts = dict(
        session.query(
            PerspectiveEvent.change_type, func.count(PerspectiveEvent.id)
        )
        .filter(PerspectiveEvent.perspective_id == perspective_id)
        .group_by(PerspectiveEvent.change_type)
        .all()
    )

    # count of tier‑change events (change_type == 'tier_change')
    tier_change_count = change_type_counts.get("tier_change", 0)

    # include the tier‑change count in the distribution dict under its own key
    tier_changes = {"tier_change": tier_change_count, **change_type_counts}

    return PerspectiveLifecycleSummary(
        perspective_id=perspective_id,
        event_count=event_count or 0,
        last_snapshot_at=last_snapshot_at,
        tier_changes=tier_changes,
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for testing
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Populate test data
    with SessionLocal() as db:
        # Perspective
        perspective = Perspective(
            id=1,
            name="test perspective",
            org_id=1,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
            created_by=1,
            description="",
            facet_filters="",
        )
        db.add(perspective)

        # Snapshots
        snapshot1 = PerspectiveSnapshot(
            id=1,
            perspective_id=1,
            membership="member",
            taken_at=datetime.datetime.utcnow() - datetime.timedelta(days=1),
        )
        snapshot2 = PerspectiveSnapshot(
            id=2,
            perspective_id=1,
            membership="member",
            taken_at=datetime.datetime.utcnow(),
        )
        db.add_all([snapshot1, snapshot2])

        # Events
        event1 = PerspectiveEvent(
            id=1,
            perspective_id=1,
            change_type="tier_change",
            old_tier="low",
            new_tier="medium",
            created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=5),
            seen=False,
            server_id=1,
        )
        event2 = PerspectiveEvent(
            id=2,
            perspective_id=1,
            change_type="other",
            old_tier="medium",
            new_tier="high",
            created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=3),
            seen=False,
            server_id=1,
        )
        event3 = PerspectiveEvent(
            id=3,
            perspective_id=1,
            change_type="tier_change",
            old_tier="high",
            new_tier="critical",
            created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=1),
            seen=False,
            server_id=1,
        )
        db.add_all([event1, event2, event3])

        db.commit()

        # Run the logic
        summary = get_perspective_lifecycle_summary(1, db)

        assert summary.event_count >= 3, "event count too low"
        assert summary.last_snapshot_at is not None, "no snapshot timestamp"
        print("PASS")