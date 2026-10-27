from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Perspective, PerspectiveEvent


async def get_perspective_event_type_mix(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    summary_stmt = (
        select(
            Perspective.id.label("perspective_id"),
            Perspective.name.label("name"),
            func.count(PerspectiveEvent.id).label("total_events"),
            func.count(func.distinct(PerspectiveEvent.server_id)).label(
                "distinct_servers"
            ),
            func.coalesce(
                func.sum(
                    case(
                        (PerspectiveEvent.seen.is_(False), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("unseen_events"),
        )
        .join(PerspectiveEvent, PerspectiveEvent.perspective_id == Perspective.id)
        .group_by(Perspective.id, Perspective.name)
        .order_by(Perspective.name, Perspective.id)
    )

    change_type_stmt = (
        select(
            PerspectiveEvent.perspective_id.label("perspective_id"),
            PerspectiveEvent.change_type.label("change_type"),
            func.count(PerspectiveEvent.id).label("count"),
        )
        .group_by(PerspectiveEvent.perspective_id, PerspectiveEvent.change_type)
    )

    transition_counts = (
        select(
            PerspectiveEvent.perspective_id.label("perspective_id"),
            PerspectiveEvent.change_type.label("change_type"),
            PerspectiveEvent.old_tier.label("old_tier"),
            PerspectiveEvent.new_tier.label("new_tier"),
            func.count(PerspectiveEvent.id).label("transition_count"),
        )
        .group_by(
            PerspectiveEvent.perspective_id,
            PerspectiveEvent.change_type,
            PerspectiveEvent.old_tier,
            PerspectiveEvent.new_tier,
        )
        .subquery()
    )

    ranked_transitions = (
        select(
            transition_counts.c.perspective_id,
            transition_counts.c.change_type,
            transition_counts.c.old_tier,
            transition_counts.c.new_tier,
            transition_counts.c.transition_count,
            func.row_number()
            .over(
                partition_by=(
                    transition_counts.c.perspective_id,
                    transition_counts.c.change_type,
                ),
                order_by=(
                    transition_counts.c.transition_count.desc(),
                    func.coalesce(transition_counts.c.old_tier, "").asc(),
                    func.coalesce(transition_counts.c.new_tier, "").asc(),
                ),
            )
            .label("transition_rank"),
        ).subquery()
    )

    dominant_transition_stmt = select(ranked_transitions).where(
        ranked_transitions.c.transition_rank == 1
    )

    summary_result = await session.execute(summary_stmt)
    change_type_result = await session.execute(change_type_stmt)
    dominant_transition_result = await session.execute(dominant_transition_stmt)

    changes: dict[str, list[dict[str, Any]]] = {}
    for row in change_type_result.mappings():
        perspective_id = str(row["perspective_id"])
        changes.setdefault(perspective_id, []).append(
            {
                "change_type": row["change_type"],
                "count": int(row["count"]),
            }
        )

    dominant_transitions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in dominant_transition_result.mappings():
        perspective_id = str(row["perspective_id"])
        change_type = row["change_type"]
        dominant_transitions[(perspective_id, change_type)] = {
            "from": row["old_tier"],
            "to": row["new_tier"],
            "count": int(row["transition_count"]),
        }

    perspectives: list[dict[str, Any]] = []
    for row in summary_result.mappings():
        perspective_id = str(row["perspective_id"])
        change_types = changes.get(perspective_id, [])
        change_types.sort(
            key=lambda item: (-item["count"], str(item["change_type"] or ""))
        )

        for change_type in change_types:
            transition = dominant_transitions.get(
                (perspective_id, change_type["change_type"])
            )
            change_type["top_transition"] = transition

        perspectives.append(
            {
                "perspective_id": perspective_id,
                "name": row["name"],
                "total_events": int(row["total_events"]),
                "distinct_servers": int(row["distinct_servers"]),
                "unseen_events": int(row["unseen_events"]),
                "change_types": change_types,
            }
        )

    return {"perspectives": perspectives}


get_event_type_mix = get_perspective_event_type_mix


if __name__ == "__main__":
    import asyncio

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    async def run_self_test() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE perspectives (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE perspective_events (
                        id TEXT PRIMARY KEY,
                        perspective_id TEXT NOT NULL,
                        server_id TEXT,
                        change_type TEXT,
                        old_tier TEXT,
                        new_tier TEXT,
                        seen BOOLEAN,
                        created_at DATETIME
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO perspectives (id, name) VALUES
                        ('p1', 'Alpha'),
                        ('p2', 'Beta')
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO perspective_events
                        (id, perspective_id, server_id, change_type,
                         old_tier, new_tier, seen, created_at)
                    VALUES
                        ('e1', 'p1', 's1', 'tier_change',
                         'TRUSTED_GENERAL', 'CAUTION_LIMITED', 1, '2024-01-01'),
                        ('e2', 'p1', 's2', 'tier_change',
                         'TRUSTED_GENERAL', 'CAUTION_LIMITED', 0, '2024-01-02'),
                        ('e3', 'p1', 's2', 'tier_change',
                         'CAUTION_LIMITED', 'BLOCKED', 1, '2024-01-03'),
                        ('e4', 'p1', 's3', 'tier_recalibration',
                         'CAUTION_LIMITED', 'TRUSTED_GENERAL', 1, '2024-01-04'),
                        ('e5', 'p2', 's4', 'tier_change',
                         'TRUSTED_GENERAL', 'CAUTION_LIMITED', 1, '2024-01-05')
                    """
                )
            )

        that_app = FastAPI()

        async def override_session():
            async with session_factory() as session:
                yield session

        that_app.dependency_overrides[get_session] = override_session

        @that_app.get("/api/perspectives/event-type-mix")
        async def event_type_mix(session: AsyncSession = Depends(get_session)):
            return await get_perspective_event_type_mix(session)

        transport = ASGITransport(app=that_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/perspectives/event-type-mix")

        assert response.status_code == 200, response.text
        payload = response.json()
        perspectives = payload["perspectives"]
        assert sum(item["total_events"] for item in perspectives) == 5
        assert sum(item["unseen_events"] for item in perspectives) == 1

        alpha = next(item for item in perspectives if item["perspective_id"] == "p1")
        tier_change = next(
            item for item in alpha["change_types"] if item["change_type"] == "tier_change"
        )
        assert tier_change["count"] == 3
        assert tier_change["top_transition"] == {
            "from": "TRUSTED_GENERAL",
            "to": "CAUTION_LIMITED",
            "count": 2,
        }

        await engine.dispose()

    asyncio.run(run_self_test())
    print("PASS")