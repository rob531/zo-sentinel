"""logic.py -- data/computation for the app3t_perspective_unseen_events_api service.

Provides a read‑only query over the real ``PerspectiveEvent`` table, returning
unseen events optionally filtered by perspective and limited to a recent
time‑window (default 7 days, clamped to 1‑90 days).  The function is pure
and operates on a supplied SQLAlchemy ``Session`` so the FastAPI router can
stay thin and the contract can be exercised directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Perspective, PerspectiveEvent
from app.db import Base  # used only in the __main__ self‑test


def get_unseen_events(
    db: Session,
    *,
    perspective_id: Optional[str] = None,
    days: int = 7,
) -> Dict[str, Any]:
    """
    Retrieve unseen ``PerspectiveEvent`` rows.

    Args:
        db: SQLAlchemy session bound to the application database.
        perspective_id: If provided, restrict results to this perspective.
        days: Number of days back to include (default 7, clamped 1‑90).

    Returns:
        A dict compatible with the router's response model:
        {
            "days": int,
            "unseen_count": int,
            "events": [
                {
                    "id": int,
                    "perspective_id": str,
                    "server_id": str,
                    "change_type": str,
                    "old_tier": str | None,
                    "new_tier": str | None,
                    "created_at": str,  # ISO‑8601 UTC
                },
                ...
            ],
        }
    """
    # Clamp days to the allowed range.
    days = max(1, min(days, 90))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(PerspectiveEvent)
        .where(
            PerspectiveEvent.seen.is_(False),
            PerspectiveEvent.created_at >= cutoff,
        )
        .order_by(PerspectiveEvent.created_at.desc())
    )
    if perspective_id is not None:
        stmt = stmt.where(PerspectiveEvent.perspective_id == perspective_id)

    rows: List[PerspectiveEvent] = db.execute(stmt).scalars().all()

    events: List[Dict[str, Any]] = []
    for ev in rows:
        events.append(
            {
                "id": ev.id,
                "perspective_id": ev.perspective_id,
                "server_id": ev.server_id,
                "change_type": ev.change_type,
                "old_tier": ev.old_tier,
                "new_tier": ev.new_tier,
                "created_at": ev.created_at.astimezone(timezone.utc).isoformat(),
            }
        )

    return {
        "days": days,
        "unseen_count": len(events),
        "events": events,
    }


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # The self‑test builds an in‑memory SQLite DB, creates the required tables,
    # seeds a few rows, invokes ``get_unseen_events`` and validates the contract.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Setup in‑memory DB with the real model metadata.
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # ------------------------------------------------------------------- #
    # Seed data.
    # ------------------------------------------------------------------- #
    with SessionLocal() as sess:
        # Two perspectives.
        p1 = Perspective(
            id="p1",
            created_by="tester",
            description="test perspective 1",
            facet_filters="{}",
            name="Perspective 1",
            org_id=None,
        )
        p2 = Perspective(
            id="p2",
            created_by="tester",
            description="test perspective 2",
            facet_filters="{}",
            name="Perspective 2",
            org_id=None,
        )
        sess.add_all([p1, p2])
        sess.flush()  # ensure FK constraints are satisfied.

        now = datetime.now(timezone.utc)

        # Four events: two unseen, two seen.
        ev1 = PerspectiveEvent(
            change_type="add",
            perspective_id="p1",
            server_id="s1",
            seen=False,
            created_at=now - timedelta(hours=1),
            old_tier=None,
            new_tier="high",
        )
        ev2 = PerspectiveEvent(
            change_type="remove",
            perspective_id="p1",
            server_id="s2",
            seen=True,
            created_at=now - timedelta(hours=2),
            old_tier="low",
            new_tier=None,
        )
        ev3 = PerspectiveEvent(
            change_type="update",
            perspective_id="p2",
            server_id="s3",
            seen=False,
            created_at=now - timedelta(hours=3),
            old_tier="medium",
            new_tier="high",
        )
        ev4 = PerspectiveEvent(
            change_type="add",
            perspective_id="p2",
            server_id="s4",
            seen=True,
            created_at=now - timedelta(hours=4),
            old_tier=None,
            new_tier="low",
        )
        sess.add_all([ev1, ev2, ev3, ev4])
        sess.commit()

        # ---------------------------------------------------------------- #
        # Execute the function under test.
        # ---------------------------------------------------------------- #
        result = get_unseen_events(sess, days=7)

        # ---------------------------------------------------------------- #
        # Assertions matching the acceptance criteria.
        # ---------------------------------------------------------------- #
        assert result["unseen_count"] == 2, "expected two unseen events"
        assert len(result["events"]) == 2, "expected two events in list"
        # All events must be unseen.
        for ev in result["events"]:
            assert ev["perspective_id"] in {"p1", "p2"}
            # The original ORM row's `seen` flag is False; we trust the query.
        # Verify ordering: newest first.
        created_times = [datetime.fromisoformat(ev["created_at"]) for ev in result["events"]]
        assert created_times == sorted(created_times, reverse=True), "events not sorted newest first"

        print("PASS")