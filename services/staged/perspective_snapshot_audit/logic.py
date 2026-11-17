from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


SNAPSHOT_STALE_HOURS = 168.0


def _membership_size(membership: Any) -> int:
    """Return the number of entries in a snapshot membership JSON list."""
    if isinstance(membership, (list, tuple)):
        return len(membership)

    if isinstance(membership, str):
        try:
            decoded = json.loads(membership)
        except (TypeError, ValueError):
            return 0
        if isinstance(decoded, (list, tuple)):
            return len(decoded)

    return 0


def _as_datetime(value: Any) -> Optional[datetime]:
    """Convert supported timestamp values to a datetime."""
    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    if isinstance(value, str):
        candidate = value.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None

    return None


def _timestamp_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _hours_since(value: Any) -> Optional[float]:
    latest = _as_datetime(value)
    if latest is None:
        return None

    if latest.tzinfo is None or latest.utcoffset() is None:
        latest = latest.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        now = datetime.now(timezone.utc)

    return (now - latest).total_seconds() / 3600.0


def _build_snapshot_audit(db: Session) -> dict:
    perspectives = (
        db.execute(select(Perspective)).scalars().all()
    )

    results = []
    for perspective in perspectives:
        snapshot_count, latest_taken_at = db.execute(
            select(
                func.count(PerspectiveSnapshot.id),
                func.max(PerspectiveSnapshot.taken_at),
            ).where(PerspectiveSnapshot.perspective_id == perspective.id)
        ).one()

        latest_membership_size = 0
        if latest_taken_at is not None:
            latest_snapshot = db.execute(
                select(PerspectiveSnapshot)
                .where(
                    PerspectiveSnapshot.perspective_id == perspective.id,
                    PerspectiveSnapshot.taken_at == latest_taken_at,
                )
                .order_by(PerspectiveSnapshot.id.desc())
                .limit(1)
            ).scalar_one_or_none()

            if latest_snapshot is not None:
                latest_membership_size = _membership_size(
                    latest_snapshot.membership
                )

        hours_since_latest = _hours_since(latest_taken_at)
        stale = (
            latest_taken_at is None
            or (
                hours_since_latest is not None
                and hours_since_latest > SNAPSHOT_STALE_HOURS
            )
        )

        results.append(
            {
                "id": str(perspective.id),
                "name": str(perspective.name or ""),
                "org_id": str(perspective.org_id or ""),
                "snapshot_count": int(snapshot_count or 0),
                "latest_taken_at": _timestamp_string(latest_taken_at),
                "latest_membership_size": latest_membership_size,
                "hours_since_latest_snapshot": (
                    round(hours_since_latest, 6)
                    if hours_since_latest is not None
                    else None
                ),
                "stale": bool(stale),
            }
        )

    return {"perspectives": results}


def get_snapshot_audit(
    session: Session = Depends(get_session),
    *,
    db: Optional[Session] = None,
) -> dict:
    active_session = db if db is not None else session
    return _build_snapshot_audit(active_session)


def get_perspective_snapshot_audit(
    session: Session = Depends(get_session),
    *,
    db: Optional[Session] = None,
) -> dict:
    active_session = db if db is not None else session
    return _build_snapshot_audit(active_session)


def get_perspectives_snapshot_audit(
    session: Session = Depends(get_session),
    *,
    db: Optional[Session] = None,
) -> dict:
    active_session = db if db is not None else session
    return _build_snapshot_audit(active_session)


if __name__ == "__main__":
    assert _membership_size([]) == 0
    assert _membership_size(["a", "b", "c"]) == 3
    assert _membership_size("[1, 2]") == 2
    assert _membership_size({"invalid": True}) == 0
    assert _membership_size(None) == 0
    assert _as_datetime("2025-01-01T00:00:00Z") is not None
    assert _hours_since("not-a-timestamp") is None
    print("PASS")