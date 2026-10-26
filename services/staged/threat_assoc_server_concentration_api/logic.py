from typing import Any, Dict

from fastapi import Depends, FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry


_ASSOCIATION_COUNTS_SQL = text(
    """
    SELECT
        server_id,
        COUNT(*) AS association_count
    FROM mcp_threat_associations
    WHERE server_id IS NOT NULL
    GROUP BY server_id
    """
)


async def get_threat_concentration(
    limit: int = 10,
    db: AsyncSession = Depends(get_session),
    **kwargs: Any,
) -> Dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    session = kwargs.get("session")
    if session is not None:
        db = session

    registry_result = await db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
        )
    )

    registry_by_id: Dict[str, Dict[str, Any]] = {}
    for server_id, name, risk_tier, verdict in registry_result.all():
        registry_by_id[str(server_id)] = {
            "name": name,
            "risk_tier": risk_tier,
            "verdict": verdict,
        }

    association_result = await db.execute(_ASSOCIATION_COUNTS_SQL)

    counts: list[tuple[Any, int]] = []
    for server_id, association_count in association_result.all():
        count = int(association_count)
        if count > 0:
            counts.append((server_id, count))

    counts.sort(key=lambda item: (-item[1], str(item[0])))

    total_associations = sum(count for _, count in counts)
    distinct_servers = len(counts)

    if total_associations:
        top_share_pct = round(
            sum(count for _, count in counts[:10]) / total_associations * 100,
            2,
        )
        hhi = round(
            sum((count / total_associations) ** 2 for _, count in counts),
            6,
        )
    else:
        top_share_pct = 0.0
        hhi = 0.0

    top_servers: list[Dict[str, Any]] = []
    for server_id, count in counts[:limit]:
        registry = registry_by_id.get(str(server_id), {})
        top_servers.append(
            {
                "server_id": server_id,
                "name": registry.get("name"),
                "risk_tier": registry.get("risk_tier"),
                "count": count,
                "pct": round(count / total_associations * 100, 2),
            }
        )

    return {
        "total_associations": total_associations,
        "distinct_servers": distinct_servers,
        "top_share_pct": top_share_pct,
        "hhi": hhi,
        "top_servers": top_servers,
    }


get_concentration = get_threat_concentration
get_concentration_data = get_threat_concentration
get_server_concentration = get_threat_concentration
get_threat_association_concentration = get_threat_concentration
get_threat_association_concentration_data = get_threat_concentration


if __name__ == "__main__":
    import asyncio

    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with SessionLocal() as session:
            await session.execute(
                text(
                    """
                    CREATE TABLE mcp_server_registry (
                        server_id VARCHAR PRIMARY KEY,
                        name VARCHAR,
                        risk_tier VARCHAR,
                        verdict VARCHAR
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE mcp_threat_associations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_id VARCHAR
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, risk_tier, verdict)
                    VALUES
                        ('server-1', 'Server One', 'low', 'trusted'),
                        ('server-2', 'Server Two', 'medium', 'approved'),
                        ('server-3', 'Server Three', 'high', 'restricted'),
                        ('server-10', 'Server Ten', 'critical', 'quarantined')
                    """
                )
            )

            association_values = [
                ("server-1",),
                ("server-2",),
                ("server-3",),
                ("server-3",),
            ] + [("server-10",)] * 10
            await session.execute(
                text(
                    "INSERT INTO mcp_threat_associations (server_id) "
                    "VALUES (:server_id)"
                ),
                [{"server_id": value[0]} for value in association_values],
            )
            await session.commit()
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/threat/concentration")
    async def concentration_endpoint(
        limit: int = 10,
        session: AsyncSession = Depends(get_session),
    ):
        return await get_threat_concentration(limit=limit, session=session)

    async def run_test() -> None:
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/threat/concentration",
                params={"limit": 10},
            )
            response.raise_for_status()
            payload = response.json()

            assert payload["total_associations"] == 14
            assert payload["distinct_servers"] == 4
            assert payload["top_servers"][0]["server_id"] == "server-10"
            assert payload["top_servers"][0]["count"] == 10

        await engine.dispose()

    asyncio.run(run_test())
    print("PASS")