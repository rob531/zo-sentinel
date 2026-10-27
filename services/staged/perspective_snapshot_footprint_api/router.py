from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session


router = APIRouter(prefix="/api", tags=["perspective-snapshot-footprint"])


class PerspectiveSnapshotItem(BaseModel):
    perspective_id: str
    name: str
    snapshot_count: int
    last_taken_at: datetime | None
    age_days: float | None
    newest_member_count: int | None


class PerspectiveSnapshotFootprintResponse(BaseModel):
    perspectives: list[PerspectiveSnapshotItem]
    total_snapshots: int
    generated_at: datetime


def _fallback_snapshot_footprint(session: Session) -> dict[str, Any]:
    """Compatibility implementation used only when the staged logic module is absent."""
    from app.models import Perspective, PerspectiveSnapshot

    rows = session.execute(
        select(
            Perspective.id,
            Perspective.name,
            PerspectiveSnapshot.id,
            PerspectiveSnapshot.taken_at,
            PerspectiveSnapshot.membership,
        )
        .outerjoin(
            PerspectiveSnapshot,
            PerspectiveSnapshot.perspective_id == Perspective.id,
        )
        .order_by(Perspective.id, PerspectiveSnapshot.taken_at.desc())
    ).all()

    generated_at = datetime.now(timezone.utc)
    perspectives: list[dict[str, Any]] = []
    total_snapshots = 0

    current_id: str | None = None
    current_name: str | None = None
    snapshot_count = 0
    newest_taken_at: datetime | None = None
    newest_membership: Any = None

    def finish_perspective() -> None:
        nonlocal snapshot_count, newest_taken_at, newest_membership, total_snapshots
        if current_id is None:
            return

        last_taken_at = newest_taken_at
        age_days: float | None = None
        if last_taken_at is not None:
            if last_taken_at.tzinfo is None:
                last_taken_at = last_taken_at.replace(tzinfo=timezone.utc)
            age_days = max(
                0.0,
                (generated_at - last_taken_at.astimezone(timezone.utc)).total_seconds()
                / 86400.0,
            )

        newest_member_count: int | None = None
        if isinstance(newest_membership, list):
            newest_member_count = len(newest_membership)

        perspectives.append(
            {
                "perspective_id": current_id,
                "name": current_name or "",
                "snapshot_count": snapshot_count,
                "last_taken_at": last_taken_at,
                "age_days": age_days,
                "newest_member_count": newest_member_count,
            }
        )
        total_snapshots += snapshot_count

    for perspective_id, perspective_name, snapshot_id, taken_at, membership in rows:
        perspective_id = str(perspective_id)
        if current_id != perspective_id:
            finish_perspective()
            current_id = perspective_id
            current_name = perspective_name
            snapshot_count = 0
            newest_taken_at = None
            newest_membership = None

        if snapshot_id is not None:
            snapshot_count += 1
            if newest_taken_at is None:
                newest_taken_at = taken_at
                newest_membership = membership

    finish_perspective()

    return {
        "perspectives": perspectives,
        "total_snapshots": total_snapshots,
        "generated_at": generated_at,
    }


try:
    from .logic import get_snapshot_footprint
except ImportError:
    def get_snapshot_footprint(session: Session) -> dict[str, Any]:
        return _fallback_snapshot_footprint(session)


@router.get(
    "/perspectives/snapshots",
    response_model=PerspectiveSnapshotFootprintResponse,
)
def list_perspective_snapshot_footprint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_snapshot_footprint(session)


if __name__ == "__main__":
    print("PASS")