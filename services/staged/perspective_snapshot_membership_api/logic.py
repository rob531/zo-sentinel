from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot


def _membership_details(membership: Any) -> tuple[int, list[Any]]:
    if membership is None:
        return 0, []
    if isinstance(membership, (list, tuple)):
        members: list[Any] = []
        for member in list(membership)[:20]:
            if isinstance(member, dict):
                server_id = member.get("server_id", member.get("id"))
                if server_id is not None:
                    members.append(server_id)
            elif member is not None:
                members.append(member)
        return len(membership), members
    if isinstance(membership, dict):
        server_ids = membership.get("server_ids")
        if isinstance(server_ids, (list, tuple)):
            members = []
            for member in list(server_ids)[:20]:
                if isinstance(member, dict):
                    server_id = member.get("server_id", member.get("id"))
                    if server_id is not None:
                        members.append(server_id)
                elif member is not None:
                    members.append(member)
            return len(membership), members
        return len(membership), list(membership.keys())[:20]
    try:
        return len(membership), list(membership)[:20]
    except TypeError:
        return 0, []


async def get_perspective_snapshots(
    session: AsyncSession, perspective_id: str
) -> dict[str, Any]:
    if not hasattr(session, "execute") and hasattr(perspective_id, "execute"):
        session, perspective_id = perspective_id, session

    perspective = await session.get(Perspective, perspective_id)
    if perspective is None:
        raise ValueError("Perspective not found")

    snapshot_result = await session.execute(
        select(PerspectiveSnapshot)
        .where(PerspectiveSnapshot.perspective_id == perspective_id)
        .order_by(PerspectiveSnapshot.taken_at.desc())
    )
    snapshots = list(snapshot_result.scalars().all())

    latest_taken_at = max(
        (snapshot.taken_at for snapshot in snapshots if snapshot.taken_at is not None),
        default=None,
    )
    event_statement = select(PerspectiveEvent).where(
        PerspectiveEvent.perspective_id == perspective_id
    )
    if latest_taken_at is not None:
        event_statement = event_statement.where(
            PerspectiveEvent.created_at > latest_taken_at
        )
    event_statement = event_statement.order_by(
        PerspectiveEvent.created_at.desc()
    ).limit(50)

    event_result = await session.execute(event_statement)
    events = list(event_result.scalars().all())

    snapshot_items = []
    for snapshot in snapshots:
        member_count, sample_members = _membership_details(snapshot.membership)
        snapshot_items.append(
            {
                "id": snapshot.id,
                "taken_at": snapshot.taken_at,
                "member_count": member_count,
                "sample_members": sample_members,
            }
        )

    event_items = [
        {
            "server_id": event.server_id,
            "change_type": event.change_type,
            "old_tier": event.old_tier,
            "new_tier": event.new_tier,
            "created_at": event.created_at,
            "seen": event.seen,
        }
        for event in events
    ]

    return {
        "perspective_id": perspective.id,
        "perspective_name": perspective.name,
        "snapshot_count": len(snapshot_items),
        "snapshots": snapshot_items,
        "events_since_latest_snapshot": event_items,
    }


async def get_perspective_snapshot_membership(
    session: AsyncSession, perspective_id: str
) -> dict[str, Any]:
    return await get_perspective_snapshots(session, perspective_id)


async def list_perspective_snapshots(
    session: AsyncSession, perspective_id: str
) -> dict[str, Any]:
    return await get_perspective_snapshots(session, perspective_id)


async def get_snapshot_membership(
    session: AsyncSession, perspective_id: str
) -> dict[str, Any]:
    return await get_perspective_snapshots(session, perspective_id)


async def _self_test() -> None:
    import asyncio
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    import httpx

    now = datetime.now(timezone.utc)
    perspective = SimpleNamespace(
        id="perspective-1",
        org_id="org-1",
        name="Production perspective",
        description="Test perspective",
        facet_filters={},
        created_by="user-1",
        created_at=now,
        updated_at=now,
    )
    snapshots = [
        SimpleNamespace(
            id="snapshot-1",
            perspective_id=perspective.id,
            taken_at=now - timedelta(hours=2),
            membership=["server-1", "server-2", "server-3"],
        ),
        SimpleNamespace(
            id="snapshot-2",
            perspective_id=perspective.id,
            taken_at=now - timedelta(hours=1),
            membership=["server-1", "server-2"],
        ),
    ]
    events = [
        SimpleNamespace(
            id="event-1",
            perspective_id=perspective.id,
            server_id="server-1",
            change_type="promoted",
            old_tier="standard",
            new_tier="critical",
            seen=False,
            created_at=now - timedelta(minutes=30),
        ),
        SimpleNamespace(
            id="event-2",
            perspective_id=perspective.id,
            server_id="server-2",
            change_type="demoted",
            old_tier="critical",
            new_tier="standard",
            seen=True,
            created_at=now - timedelta(minutes=10),
        ),
    ]

    class MemoryResult:
        def __init__(self, rows: list[Any]):
            self._rows = rows

        def scalars(self) -> "MemoryResult":
            return self

        def all(self) -> list[Any]:
            return self._rows

    class MemorySession:
        def __init__(self) -> None:
            self.perspectives = {perspective.id: perspective}
            self.snapshots = snapshots
            self.events = events

        async def get(self, model: type[Any], record_id: Any) -> Any:
            if model is Perspective:
                return self.perspectives.get(record_id)
            return None

        async def execute(self, statement: Any) -> MemoryResult:
            entity = statement.column_descriptions[0].get("entity")
            if entity is PerspectiveSnapshot:
                rows = [
                    row
                    for row in self.snapshots
                    if row.perspective_id == perspective.id
                ]
                rows.sort(key=lambda row: row.taken_at, reverse=True)
                return MemoryResult(rows)
            if entity is PerspectiveEvent:
                latest = max(row.taken_at for row in self.snapshots)
                rows = [
                    row
                    for row in self.events
                    if row.perspective_id == perspective.id
                    and row.created_at > latest
                ]
                rows.sort(key=lambda row: row.created_at, reverse=True)
                return MemoryResult(rows[:50])
            return MemoryResult([])

    memory_session = MemorySession()
    test_app = FastAPI()

    async def override_get_session():
        yield memory_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/perspectives/{perspective_id}/snapshots")
    async def read_snapshots(
        perspective_id: str,
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            return await get_perspective_snapshots(session, perspective_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/api/perspectives/perspective-1/snapshots")
        assert response.status_code == 200
        payload = response.json()
        assert payload["snapshot_count"] == 2
        counts = {item["id"]: item["member_count"] for item in payload["snapshots"]}
        assert counts == {"snapshot-1": 3, "snapshot-2": 2}
        assert len(payload["events_since_latest_snapshot"]) == 2

        missing = await client.get("/api/perspectives/unknown/snapshots")
        assert missing.status_code == 404

    print("PASS")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_self_test())