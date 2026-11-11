from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveSnapshot


def _decode_membership(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            try:
                return json.loads(value.decode("utf-8"))
            except (AttributeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return None
    return value


def _membership_entries(value: Any) -> list[Any]:
    value = _decode_membership(value)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("members", "membership", "entries", "items"):
            candidate = value.get(key)
            if isinstance(candidate, (list, dict)):
                return list(candidate.values()) if isinstance(candidate, dict) else candidate
        if any(
            key in value
            for key in (
                "server_id",
                "server",
                "present",
                "missing",
                "status",
                "state",
            )
        ):
            return [value]
        return list(value.values())
    return []


def _is_present(entry: Any) -> bool:
    entry = _decode_membership(entry)
    if isinstance(entry, dict):
        if "present" in entry:
            return bool(entry["present"])
        if "missing" in entry:
            return not bool(entry["missing"])
        for key in ("status", "state", "membership_status"):
            status = entry.get(key)
            if isinstance(status, str):
                normalized = status.strip().lower()
                if normalized in {"missing", "absent", "not_present", "not present", "false"}:
                    return False
                if normalized in {"present", "active", "included", "true"}:
                    return True
        for key in ("present", "exists", "available", "active"):
            if key in entry:
                return bool(entry[key])
        return True
    if isinstance(entry, str):
        normalized = entry.strip().lower()
        if normalized in {"missing", "absent", "not_present", "not present"}:
            return False
    return True


def _snapshot_composition(membership: Any) -> tuple[int, int, int]:
    entries = _membership_entries(membership)
    present_count = sum(1 for entry in entries if _is_present(entry))
    member_count = len(entries)
    missing_count = member_count - present_count
    return member_count, present_count, missing_count


def get_snapshot_sizes(
    session: Session | None = None,
    perspective_id: str | None = None,
    days: int = 30,
    db: Session | None = None,
) -> dict[str, Any]:
    session = session or db
    if session is None:
        raise ValueError("A database session is required")

    days = max(1, min(int(days), 365))
    cutoff = datetime.utcnow() - timedelta(days=days)

    statement = (
        select(PerspectiveSnapshot.taken_at, PerspectiveSnapshot.membership)
        .join(Perspective, Perspective.id == PerspectiveSnapshot.perspective_id)
        .where(PerspectiveSnapshot.taken_at >= cutoff)
        .order_by(PerspectiveSnapshot.taken_at.asc())
    )
    if perspective_id is not None:
        statement = statement.where(Perspective.id == perspective_id)

    snapshots = []
    for taken_at, membership in session.execute(statement):
        member_count, present_count, missing_count = _snapshot_composition(membership)
        snapshots.append(
            {
                "taken_at": taken_at.isoformat() if hasattr(taken_at, "isoformat") else taken_at,
                "member_count": member_count,
                "present_count": present_count,
                "missing_count": missing_count,
            }
        )

    return {
        "days": days,
        "perspective_id": perspective_id,
        "snapshots": snapshots,
    }


def get_perspective_snapshot_sizes(
    session: Session | None = None,
    perspective_id: str | None = None,
    days: int = 30,
    db: Session | None = None,
) -> dict[str, Any]:
    return get_snapshot_sizes(
        session=session,
        perspective_id=perspective_id,
        days=days,
        db=db,
    )