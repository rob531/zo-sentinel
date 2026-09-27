"""services/staged/perspective_snapshot_timeline/logic.py"""

from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import Depends
from pydantic import BaseModel, Field

from sqlalchemy.orm import Session

from app.db import get_session
from app.models import PerspectiveSnapshot, Perspective, Base  # noqa: F401


class SnapshotItem(BaseModel):
    taken_at: datetime.datetime = Field(..., description="When the snapshot was taken")
    server_count: int = Field(..., description="Number of servers in the snapshot")
    added: List[int] = Field(default_factory=list, description="Servers added since previous snapshot")
    removed: List[int] = Field(default_factory=list, description="Servers that will be removed in next snapshot")

    class Config:
        orm_mode = True


class TimelineResponse(BaseModel):
    perspective_id: int = Field(..., description="ID of the perspective")
    snapshots: List[SnapshotItem] = Field(default_factory=list, description="Chronological snapshot timeline")

    class Config:
        orm_mode = True


def _compute_diff(
    current: set[int],
    previous: Optional[set[int]],
    next_: Optional[set[int]],
) -> tuple[List[int], List[int]]:
    """Return (added, removed) where:
    - added  = servers present in *current* but not in *previous* (if previous exists)
    - removed = servers present in *current* but not in *next* (if next exists)
    """
    added = list(current - previous) if previous is not None else list(current)
    removed = list(current - next_) if next_ is not None else []
    added.sort()
    removed.sort()
    return added, removed


def get_perspective_snapshot_timeline(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> TimelineResponse:
    """Return the timeline of snapshots for a perspective with membership diffs."""
    snapshots = (
        session.query(PerspectiveSnapshot)
        .filter(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.asc())
        .all()
    )

    items: List[SnapshotItem] = []
    for idx, snap in enumerate(snapshots):
        current_set = set(snap.membership or [])
        prev_set = set(snapshots[idx - 1].membership or []) if idx > 0 else None
        next_set = set(snapshots[idx + 1].membership or []) if idx + 1 < len(snapshots) else None

        added, removed = _compute_diff(current_set, prev_set, next_set)

        items.append(
            SnapshotItem(
                taken_at=snap.taken_at,
                server_count=len(current_set),
                added=added,
                removed=removed,
            )
        )

    return TimelineResponse(perspective_id=perspective_id, snapshots=items)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed data
    sess = SessionLocal()
    try:
        # Minimal perspective (only required columns)
        perspective = Perspective(
            id=1,
            name="test",
            org_id=1,
            created_at=datetime.datetime.utcnow(),
            created_by=1,
            description="",
            facet_filters="{}",
            updated_at=datetime.datetime.utcnow(),
        )
        sess.add(perspective)

        # Helper to create snapshots
        def add_snapshot(taken_at: datetime.datetime, membership: List[int]) -> None:
            snap = PerspectiveSnapshot(
                perspective_id=1,
                taken_at=taken_at,
                membership=membership,
            )
            sess.add(snap)

        now = datetime.datetime.utcnow()
        add_snapshot(now - datetime.timedelta(days=2), [1, 2])  # A
        add_snapshot(now - datetime.timedelta(days=1), [2, 3])  # B
        add_snapshot(now, [3])                                 # C

        sess.commit()

        # Invoke the logic
        result = get_perspective_snapshot_timeline(perspective_id=1, session=sess)

        # Assertions per acceptance criteria
        assert result.perspective_id == 1
        assert len(result.snapshots) == 3

        # Snapshot[1] corresponds to the middle snapshot (B)
        snap_b = result.snapshots[1]
        assert snap_b.added == [3], f"expected added [3], got {snap_b.added}"
        assert snap_b.removed == [2], f"expected removed [2], got {snap_b.removed}"

        print("PASS")
    finally:
        sess.close()