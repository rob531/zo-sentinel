from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry, VulnLink


async def get_unlinked_servers(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    total_stmt = select(func.count(func.distinct(McpServerRegistry.server_id)))
    linked_stmt = (
        select(func.count(func.distinct(VulnLink.server_id)))
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .where(VulnLink.id.is_not(None))
    )
    unlinked_count_stmt = (
        select(func.count(func.distinct(McpServerRegistry.server_id)))
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .where(VulnLink.id.is_(None))
    )
    unlinked_stmt = (
        select(McpServerRegistry)
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .where(VulnLink.id.is_(None))
        .order_by(
            McpServerRegistry.last_seen.desc().nulls_last(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(100)
    )

    unlinked_marker = case(
        (VulnLink.id.is_(None), McpServerRegistry.server_id),
        else_=None,
    )
    breakdown_stmt = (
        select(
            McpServerRegistry.registry_source,
            func.count(func.distinct(unlinked_marker)),
        )
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source.asc().nulls_last())
    )

    total_result = await session.execute(total_stmt)
    linked_result = await session.execute(linked_stmt)
    unlinked_count_result = await session.execute(unlinked_count_stmt)
    unlinked_result = await session.execute(unlinked_stmt)
    breakdown_result = await session.execute(breakdown_stmt)

    total_servers = int(total_result.scalar_one() or 0)
    linked_servers = int(linked_result.scalar_one() or 0)
    unlinked_servers = int(unlinked_count_result.scalar_one() or 0)
    coverage_pct = (
        round((linked_servers / total_servers) * 100.0, 2)
        if total_servers
        else 0.0
    )

    unlinked = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "verdict": row.verdict,
            "risk_tier": row.risk_tier,
            "last_seen": row.last_seen,
        }
        for row in unlinked_result.scalars().all()
    ]

    by_registry_source = [
        {
            "registry_source": source,
            "unlinked_count": int(count),
        }
        for source, count in breakdown_result.all()
    ]

    return {
        "total_servers": total_servers,
        "linked_servers": linked_servers,
        "unlinked_servers": unlinked_servers,
        "coverage_pct": coverage_pct,
        "unlinked": unlinked,
        "by_registry_source": by_registry_source,
    }


def _self_test() -> None:
    from contextlib import asynccontextmanager
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: McpServerRegistry.metadata.create_all(
                    sync_connection,
                    tables=[McpServerRegistry.__table__, VulnLink.__table__],
                )
            )

        now = datetime.now(timezone.utc)
        async with session_factory() as session:
            servers = [
                McpServerRegistry(
                    server_id=f"server-{index}",
                    name=f"Test Server {index}",
                    description=f"Test server {index}",
                    registry_source="source-a" if index < 3 else "source-b",
                    url=f"https://example.test/server-{index}",
                    verdict="trusted",
                    risk_tier="low",
                    confidence=1.0,
                    trust_score=1.0,
                    scan_count=1,
                    first_seen=now,
                    last_seen=now,
                )
                for index in range(1, 6)
            ]
            session.add_all(servers)
            await session.flush()
            session.add_all(
                [
                    VulnLink(
                        advisory_id="advisory-1",
                        server_id="server-1",
                        linked_at=now,
                        match_basis="test",
                        match_confidence=1.0,
                        match_value="server-1",
                    ),
                    VulnLink(
                        advisory_id="advisory-2",
                        server_id="server-2",
                        linked_at=now,
                        match_basis="test",
                        match_confidence=1.0,
                        match_value="server-2",
                    ),
                ]
            )
            await session.commit()

        yield
        await engine.dispose()

    test_app = FastAPI(lifespan=lifespan)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/vuln/unlinked-servers")
    async def unlinked_servers_endpoint(payload=Depends(get_unlinked_servers)):
        return payload

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/unlinked-servers")
        assert response.status_code == 200
        body = response.json()
        assert body["linked_servers"] == 2
        assert body["unlinked_servers"] == 3
        assert body["coverage_pct"] == 40.0
        assert len(body["unlinked"]) == 3

    print("PASS")


if __name__ == "__main__":
    _self_test()