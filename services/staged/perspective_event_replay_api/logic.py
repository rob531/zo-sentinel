from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api")


def _parse_since(since: datetime | str | None) -> datetime | None:
    if since is None:
        return None
    if isinstance(since, datetime):
        return since
    return datetime.fromisoformat(since.replace("Z", "+00:00"))


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def get_perspective_events(
    session: AsyncSession,
    perspective_id: str,
    since: datetime | str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    parsed_since = _parse_since(since)

    statement = (
        select(
            PerspectiveEvent.id,
            PerspectiveEvent.server_id,
            McpServerRegistry.name.label("server_name"),
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.seen,
            PerspectiveEvent.created_at,
        )
        .select_from(PerspectiveEvent)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == PerspectiveEvent.server_id,
        )
        .where(PerspectiveEvent.perspective_id == perspective_id)
    )

    if parsed_since is not None:
        statement = statement.where(PerspectiveEvent.created_at > parsed_since)

    statement = (
        statement.order_by(
            PerspectiveEvent.created_at.desc(),
            PerspectiveEvent.id.desc(),
        )
        .limit(limit)
    )

    result = await session.execute(statement)
    rows = result.all()

    return [
        {
            "id": int(row.id),
            "server_id": str(row.server_id),
            "server_name": row.server_name,
            "change_type": row.change_type,
            "old_tier": row.old_tier,
            "new_tier": row.new_tier,
            "seen": bool(row.seen),
            "created_at": _isoformat(row.created_at),
        }
        for row in rows
    ]


@router.get("/perspectives/{perspective_id}/events")
async def read_perspective_events(
    perspective_id: str,
    since: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    events = await get_perspective_events(
        session=session,
        perspective_id=perspective_id,
        since=since,
        limit=limit,
    )
    return {
        "perspective_id": perspective_id,
        "since": since.isoformat() if since is not None else None,
        "count": len(events),
        "events": events,
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    class EventModel(BaseModel):
        id: int
        server_id: str
        server_name: str | None
        change_type: str
        old_tier: str | None
        new_tier: str | None
        seen: bool
        created_at: str

    class EventsModel(BaseModel):
        perspective_id: str
        since: str | None
        count: int
        events: list[EventModel]

    async def run_self_test() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: McpServerRegistry.__table__.create(
                    sync_connection, checkfirst=True
                )
            )
            await connection.run_sync(
                lambda sync_connection: PerspectiveEvent.__table__.create(
                    sync_connection, checkfirst=True
                )
            )

        now = datetime.now().astimezone()
        async with session_factory() as session:
            session.add(
                McpServerRegistry(
                    server_id="server-self-test",
                    name="Self Test Server",
                )
            )
            session.add_all(
                [
                    PerspectiveEvent(
                        id=1,
                        perspective_id="perspective-one",
                        server_id="server-self-test",
                        change_type="tier_changed",
                        old_tier="low",
                        new_tier="medium",
                        seen=False,
                        created_at=now,
                    ),
                    PerspectiveEvent(
                        id=2,
                        perspective_id="perspective-one",
                        server_id="server-self-test",
                        change_type="tier_changed",
                        old_tier="medium",
                        new_tier="high",
                        seen=True,
                        created_at=now.replace(microsecond=now.microsecond + 1),
                    ),
                    PerspectiveEvent(
                        id=3,
                        perspective_id="perspective-two",
                        server_id="server-self-test",
                        change_type="observed",
                        old_tier=None,
                        new_tier="high",
                        seen=True,
                        created_at=now,
                    ),
                ]
            )
            await session.commit()

        async def override_get_session():
            async with session_factory() as session:
                yield session

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = override_get_session

        @test_app.get(
            "/api/perspectives/{perspective_id}/events",
            response_model=EventsModel,
        )
        async def test_endpoint(
            perspective_id: str,
            since: datetime | None = None,
            limit: int = Query(default=100, ge=1, le=500),
            session: AsyncSession = Depends(get_session),
        ) -> dict[str, Any]:
            events = await get_perspective_events(
                session=session,
                perspective_id=perspective_id,
                since=since,
                limit=limit,
            )
            return {
                "perspective_id": perspective_id,
                "since": since.isoformat() if since is not None else None,
                "count": len(events),
                "events": events,
            }

        with TestClient(test_app) as client:
            response = client.get("/api/perspectives/perspective-one/events")
            assert response.status_code == 200
            payload = response.json()
            assert payload["count"] == 2
            assert len(payload["events"]) == 2
            assert payload["events"][0]["server_name"] == "Self Test Server"
            assert isinstance(payload["events"][0]["seen"], bool)

        await engine.dispose()

    import asyncio

    asyncio.run(run_self_test())
    print("PASS")