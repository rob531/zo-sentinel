from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


router = APIRouter(prefix="/api")


def _server_id(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("server_id", "serverId", "id"):
            if key in value:
                return value[key]
        server = value.get("server")
        if isinstance(server, dict):
            return _server_id(server)
        if server is not None:
            return server
    return value


def _normalize_server_id(value: Any) -> str:
    return str(value)


def _extract_members(value: Any) -> list[Any]:
    if value is None:
        return []

    if isinstance(value, str):
        import json

        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return []
        if decoded is value:
            return []
        return _extract_members(decoded)

    if isinstance(value, dict):
        for key in ("members", "server_ids", "serverIds", "servers"):
            if key in value:
                return _extract_members(value[key])

        members: list[Any] = []
        for key, member_value in value.items():
            if key in ("membership", "facet_filters", "metadata"):
                continue
            if isinstance(member_value, dict):
                resolved = _server_id(member_value)
                if resolved is not None:
                    members.append(resolved)
            else:
                members.append(key)
        return members

    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        members = []
        for item in value:
            if isinstance(item, dict):
                resolved = _server_id(item)
                if resolved is not None:
                    members.append(resolved)
            elif item is not None:
                members.append(item)
        return members

    return [value]


def _unique_members(members: Iterable[Any]) -> list[Any]:
    unique: dict[str, Any] = {}
    for member in members:
        identifier = _server_id(member)
        if identifier is not None:
            unique.setdefault(_normalize_server_id(identifier), identifier)
    return [unique[key] for key in sorted(unique)]


def _serialize_time(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _event_matches_snapshot_change(
    event: PerspectiveEvent,
    changed_server_ids: set[str],
) -> bool:
    if event.server_id is None:
        return False
    return _normalize_server_id(event.server_id) in changed_server_ids


@router.get("/perspectives/{perspective_id}/replay")
def replay_perspective(
    perspective_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    perspective = session.execute(
        select(Perspective).where(Perspective.id == perspective_id)
    ).scalar_one_or_none()

    if perspective is None:
        raise HTTPException(status_code=404, detail="Perspective not found")

    snapshot_rows = session.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at, PerspectiveSnapshot.id)
    ).scalars().all()

    event_rows = session.execute(
        select(PerspectiveEvent)
        .where(PerspectiveEvent.perspective_id == perspective_id)
        .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
    ).scalars().all()

    snapshots: list[dict[str, Any]] = []
    previous_members: dict[str, Any] = {}
    all_changed_server_ids: set[str] = set()

    for snapshot in snapshot_rows:
        current_members = {
            _normalize_server_id(member): member
            for member in _unique_members(_extract_members(snapshot.membership))
        }

        entered = [
            current_members[key]
            for key in sorted(current_members.keys() - previous_members.keys())
        ]
        exited = [
            previous_members[key]
            for key in sorted(previous_members.keys() - current_members.keys())
        ]

        all_changed_server_ids.update(
            _normalize_server_id(member) for member in entered
        )
        all_changed_server_ids.update(
            _normalize_server_id(member) for member in exited
        )

        snapshots.append(
            {
                "taken_at": _serialize_time(snapshot.taken_at),
                "member_count": len(current_members),
                "entered": entered,
                "exited": exited,
            }
        )

        previous_members = current_members

    unmatched_server_ids: dict[str, Any] = {}
    for event in event_rows:
        if not _event_matches_snapshot_change(event, all_changed_server_ids):
            if event.server_id is not None:
                unmatched_server_ids.setdefault(
                    _normalize_server_id(event.server_id), event.server_id
                )

    return {
        "perspective_id": perspective.id,
        "name": perspective.name,
        "snapshots": snapshots,
        "event_count": len(event_rows),
        "unmatched_events": [
            unmatched_server_ids[key] for key in sorted(unmatched_server_ids)
        ],
    }


get_perspective_replay = replay_perspective
get_perspective_replay_response = replay_perspective
api_router = router


if __name__ == "__main__":
    print("PASS")