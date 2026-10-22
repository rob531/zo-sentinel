from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent


async def get_unseen_events(
    session: AsyncSession,
    perspective_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    limit = max(0, min(int(limit), 500))

    count_result = await session.execute(
        select(func.count())
        .select_from(PerspectiveEvent)
        .where(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen.is_(False),
        )
    )
    unseen_count = int(count_result.scalar_one())

    if limit == 0:
        return {"unseen_count": unseen_count, "events": []}

    result = await session.execute(
        select(
            PerspectiveEvent.id,
            PerspectiveEvent.server_id,
            McpServerRegistry.name,
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
            PerspectiveEvent.seen,
            PerspectiveEvent.created_at,
            McpServerRegistry.risk_tier,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == PerspectiveEvent.server_id,
        )
        .where(
            PerspectiveEvent.perspective_id == perspective_id,
            PerspectiveEvent.seen.is_(False),
        )
        .order_by(
            PerspectiveEvent.created_at.desc(),
            PerspectiveEvent.id.desc(),
        )
        .limit(limit)
    )

    events: list[dict[str, Any]] = []
    for row in result.all():
        created_at = row.created_at
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()
        else:
            created_at = str(created_at)

        events.append(
            {
                "id": int(row.id),
                "server_id": str(row.server_id),
                "server_name": str(row.name or ""),
                "change_type": str(row.change_type),
                "old_tier": row.old_tier,
                "new_tier": row.new_tier,
                "seen": bool(row.seen),
                "created_at": created_at,
            }
        )

    return {"unseen_count": unseen_count, "events": events}


async def fetch_unseen_events(
    session: AsyncSession,
    perspective_id: str,
    limit: int = 100,
) -> tuple[int, list[dict[str, Any]]]:
    result = await get_unseen_events(session, perspective_id, limit)
    return result["unseen_count"], result["events"]


if __name__ == "__main__":
    import asyncio

    import httpx

    async def self_test() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async def override_get_session():
            async with session_factory() as session:
                yield session

        events_table = PerspectiveEvent.__tablename__
        registry_table = McpServerRegistry.__tablename__

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    CREATE TABLE perspectives (
                        id TEXT PRIMARY KEY,
                        name TEXT,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    f"""
                    CREATE TABLE {events_table} (
                        id INTEGER PRIMARY KEY,
                        perspective_id TEXT NOT NULL,
                        server_id TEXT NOT NULL,
                        change_type TEXT NOT NULL,
                        old_tier TEXT,
                        new_tier TEXT,
                        seen BOOLEAN NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    f"""
                    CREATE TABLE {registry_table} (
                        server_id TEXT PRIMARY KEY,
                        name TEXT,
                        risk_tier TEXT
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO perspectives (id, name) VALUES (:id, :name)"
                ),
                {"id": "perspective-1", "name": "Test Perspective"},
            )
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {registry_table} (server_id, name, risk_tier)
                    VALUES (:server_id, :name, :risk_tier)
                    """
                ),
                [
                    {"server_id": "server-a", "name": "Server A", "risk_tier": "high"},
                    {"server_id": "server-b", "name": "Server B", "risk_tier": "medium"},
                    {"server_id": "server-c", "name": "Server C", "risk_tier": "low"},
                ],
            )
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {events_table}
                        (id, perspective_id, server_id, change_type, old_tier,
                         new_tier, seen, created_at)
                    VALUES
                        (:id, :perspective_id, :server_id, :change_type, :old_tier,
                         :new_tier, :seen, :created_at)
                    """
                ),
                [
                    {
                        "id": 1,
                        "perspective_id": "perspective-1",
                        "server_id": "server-a",
                        "change_type": "tier_changed",
                        "old_tier": "low",
                        "new_tier": "medium",
                        "seen": 0,
                        "created_at": "2025-01-01T00:00:00",
                    },
                    {
                        "id": 2,
                        "perspective_id": "perspective-1",
                        "server_id": "server-b",
                        "change_type": "tier_changed",
                        "old_tier": "medium",
                        "new_tier": "high",
                        "seen": 0,
                        "created_at": "2025-01-02T00:00:00",
                    },
                    {
                        "id": 3,
                        "perspective_id": "perspective-1",
                        "server_id": "server-c",
                        "change_type": "tier_changed",
                        "old_tier": "low",
                        "new_tier": "low",
                        "seen": 1,
                        "created_at": "2025-01-03T00:00:00",
                    },
                ],
            )

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = override_get_session

        @test_app.get("/api/perspectives/{perspective_id}/unseen-events")
        async def unseen_events_endpoint(
            perspective_id: str,
            limit: int = 100,
            session: AsyncSession = Depends(get_session),
        ):
            result = await get_unseen_events(session, perspective_id, limit)
            return {
                "perspective_id": perspective_id,
                **result,
            }

        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/perspectives/perspective-1/unseen-events"
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["unseen_count"] == 2
            assert [event["server_id"] for event in payload["events"]] == [
                "server-b",
                "server-a",
            ]

        await engine.dispose()

    asyncio.run(self_test())
    print("PASS")