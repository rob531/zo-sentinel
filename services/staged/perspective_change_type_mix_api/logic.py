from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


def _within_days(created_at: Any, cutoff: datetime) -> bool:
    if created_at is None:
        return False

    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False

    if not isinstance(created_at, datetime):
        return False

    if created_at.tzinfo is None:
        return created_at >= cutoff.replace(tzinfo=None)

    return created_at >= cutoff


async def get_change_type_mix(
    db: Any,
    perspective_id: str | None = None,
    days: int = 30,
) -> dict[str, Any]:
    statement = (
        select(PerspectiveEvent, Perspective)
        .join(Perspective, Perspective.id == PerspectiveEvent.perspective_id)
        .order_by(PerspectiveEvent.created_at, PerspectiveEvent.id)
    )

    if perspective_id is not None:
        statement = statement.where(PerspectiveEvent.perspective_id == perspective_id)

    result = await db.execute(statement)
    rows = result.all()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    perspectives: dict[str, dict[str, Any]] = {}
    change_type_events: dict[str, dict[str, int]] = defaultdict(dict)
    change_type_servers: dict[str, dict[str, set[str]]] = defaultdict(dict)
    transition_events: dict[str, dict[str, int]] = defaultdict(dict)

    for event, perspective in rows:
        if not _within_days(event.created_at, cutoff):
            continue

        perspective_key = str(perspective.id)
        event_change_type = str(event.change_type)
        server_key = str(event.server_id) if event.server_id is not None else None

        perspective_entry = perspectives.setdefault(
            perspective_key,
            {
                "perspective_id": perspective_key,
                "name": str(perspective.name),
                "change_types": [],
                "transitions": [],
            },
        )

        change_type_events[perspective_key][event_change_type] = (
            change_type_events[perspective_key].get(event_change_type, 0) + 1
        )

        if server_key is not None:
            servers = change_type_servers[perspective_key].setdefault(
                event_change_type, set()
            )
            servers.add(server_key)

        if event.old_tier is not None and event.new_tier is not None:
            transition = f"{event.old_tier}->{event.new_tier}"
            transition_events[perspective_key][transition] = (
                transition_events[perspective_key].get(transition, 0) + 1
            )

        perspective_entry["name"] = str(perspective.name)

    total_events = 0

    for perspective_key, entry in perspectives.items():
        change_types = []
        for change_type, events in change_type_events[perspective_key].items():
            total_events += events
            change_types.append(
                {
                    "change_type": change_type,
                    "events": events,
                    "servers": len(
                        change_type_servers[perspective_key].get(change_type, set())
                    ),
                }
            )

        change_types.sort(key=lambda item: (-item["events"], item["change_type"]))

        transitions = [
            {"transition": transition, "events": events}
            for transition, events in transition_events[perspective_key].items()
        ]
        transitions.sort(key=lambda item: (-item["events"], item["transition"]))

        entry["change_types"] = change_types
        entry["transitions"] = transitions

    ordered_perspectives = sorted(
        perspectives.values(),
        key=lambda item: (item["name"], item["perspective_id"]),
    )

    return {
        "perspectives": ordered_perspectives,
        "total_events": total_events,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_perspective_change_type_mix(
    db: Any,
    perspective_id: str | None = None,
    days: int = 30,
) -> dict[str, Any]:
    return await get_change_type_mix(db, perspective_id=perspective_id, days=days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    class _MemoryResult:
        def __init__(self, rows: list[tuple[PerspectiveEvent, Perspective]]) -> None:
            self._rows = rows

        def all(self) -> list[tuple[PerspectiveEvent, Perspective]]:
            return list(self._rows)

    class _MemorySession:
        def __init__(
            self, rows: list[tuple[PerspectiveEvent, Perspective]]
        ) -> None:
            self._rows = rows

        async def execute(self, statement: Any) -> _MemoryResult:
            return _MemoryResult(self._rows)

    now = datetime.now(timezone.utc)
    perspective_one = Perspective(
        id="perspective-one",
        name="Production",
        org_id="org-one",
    )
    perspective_two = Perspective(
        id="perspective-two",
        name="Development",
        org_id="org-one",
    )
    events = [
        PerspectiveEvent(
            id="event-one",
            perspective_id="perspective-one",
            server_id="server-one",
            change_type="add",
            old_tier=None,
            new_tier="tier1",
            seen=True,
            created_at=now,
        ),
        PerspectiveEvent(
            id="event-two",
            perspective_id="perspective-one",
            server_id="server-two",
            change_type="add",
            old_tier=None,
            new_tier="tier1",
            seen=True,
            created_at=now,
        ),
        PerspectiveEvent(
            id="event-three",
            perspective_id="perspective-two",
            server_id="server-three",
            change_type="remove",
            old_tier="tier1",
            new_tier=None,
            seen=True,
            created_at=now,
        ),
        PerspectiveEvent(
            id="event-four",
            perspective_id="perspective-two",
            server_id="server-four",
            change_type="tier_upgrade",
            old_tier="tier1",
            new_tier="tier2",
            seen=True,
            created_at=now,
        ),
    ]

    memory_session = _MemorySession(
        [(event, perspective_one) for event in events[:2]]
        + [(event, perspective_two) for event in events[2:]]
    )

    that_app = FastAPI()

    @that_app.get("/api/perspectives/change-type-mix")
    async def change_type_mix_endpoint(
        perspective_id: str | None = None,
        days: int = 30,
        session: Any = Depends(get_session),
    ) -> dict[str, Any]:
        return await get_change_type_mix(
            session,
            perspective_id=perspective_id,
            days=days,
        )

    async def override_get_session() -> Any:
        yield memory_session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/perspectives/change-type-mix")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_events"] == 4
    assert any(
        len(perspective["change_types"]) == 2
        for perspective in payload["perspectives"]
    )
    assert any(
        transition["transition"] == "tier1->tier2"
        and transition["events"] == 1
        for perspective in payload["perspectives"]
        for transition in perspective["transitions"]
    )

    print("PASS")