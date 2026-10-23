from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


class MeshMemoryEndpoint(BaseModel):
    server_id: str
    memory_type: str | None = None
    content: str | None = None
    embedding: list[float] | None = None
    created_at: str | None = None


class ScoreDisputesEndpoint(BaseModel):
    dispute_id: str
    server_id: str
    axis: str | None = None
    status: str | None = None


_server_registry_cache: dict[str, McpServerRegistry] = {}


async def mesh_memory_endpoint(
    server_id: str,
    session: AsyncSession = Depends(get_session),
) -> MeshMemoryEndpoint | None:
    result = await session.execute(
        text("SELECT server_id, memory_type, content, embedding, created_at "
             "FROM mesh_memory WHERE server_id = :server_id LIMIT 1"),
        {"server_id": server_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return MeshMemoryEndpoint(
        server_id=row[0],
        memory_type=row[1],
        content=row[2],
        embedding=row[3],
        created_at=str(row[4]) if row[4] else None,
    )


async def get_mesh_memory_endpoint(
    server_id: str,
) -> MeshMemoryEndpoint | None:
    async for session in get_session():
        return await mesh_memory_endpoint(server_id, session)


async def get_score_disputes_endpoint(
    session: AsyncSession = Depends(get_session),
) -> list[ScoreDisputesEndpoint]:
    result = await session.execute(
        text("SELECT dispute_id, server_id, axis, status FROM mcp_score_disputes LIMIT 100")
    )
    rows = result.fetchall()
    return [
        ScoreDisputesEndpoint(
            dispute_id=row[0],
            server_id=row[1],
            axis=row[2],
            status=row[3],
        )
        for row in rows
    ]


async def signal_scores_endpoint(
    server_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(
        text("SELECT server_id, axis, score FROM mcp_signal_scores WHERE server_id = :server_id"),
        {"server_id": server_id},
    )
    rows = result.fetchall()
    return {
        "server_id": server_id,
        "scores": [{"axis": row[1], "score": row[2]} for row in rows],
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _server_registry_cache.clear()
    yield


the_app = FastAPI(lifespan=lifespan)


@the_app.get("/mesh_memory/{server_id}")
async def get_mesh_memory(
    server_id: str,
    session: AsyncSession = Depends(get_session),
) -> MeshMemoryEndpoint | dict:
    result = await mesh_memory_endpoint(server_id, session)
    if result is None:
        return {"error": "not_found"}
    return result


@the_app.get("/score_disputes")
async def list_score_disputes(
    session: AsyncSession = Depends(get_session),
) -> list[ScoreDisputesEndpoint]:
    return await get_score_disputes_endpoint(session)


@the_app.get("/signal_scores/{server_id}")
async def get_signal_scores(
    server_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await signal_scores_endpoint(server_id, session)


if __name__ == "__main__":
    import asyncio
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    _test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async def init_test_db():
        async with _test_engine.begin() as conn:
            await conn.execute(text("CREATE TABLE mcp_signal_scores (server_id TEXT, axis TEXT, score REAL)"))
            await conn.execute(text("CREATE TABLE mesh_memory (server_id TEXT, memory_type TEXT, content TEXT, embedding TEXT, created_at TEXT)"))
            await conn.execute(text("INSERT INTO mcp_signal_scores VALUES ('test-srv', 'trust', 0.85)"))
            await conn.execute(text("INSERT INTO mesh_memory VALUES ('test-srv', 'context', 'test content', NULL, '2024-01-01')"))

    async def run_self_test():
        await init_test_db()

        test_session_factory = async_sessionmaker(
            _test_engine, expire_on_commit=False
        )

        async def override_get_session():
            async with test_session_factory() as session:
                yield session

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = override_get_session

        @test_app.get("/mesh_memory/{server_id}")
        async def get_mesh_memory_test(server_id: str, session: AsyncSession = Depends(get_session)):
            return await mesh_memory_endpoint(server_id, session)

        @test_app.get("/score_disputes")
        async def list_disputes_test(session: AsyncSession = Depends(get_session)):
            return await get_score_disputes_endpoint(session)

        @test_app.get("/signal_scores/{server_id}")
        async def get_scores_test(server_id: str, session: AsyncSession = Depends(get_session)):
            return await signal_scores_endpoint(server_id, session)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            resp = await client.get("/mesh_memory/test-srv")
            assert resp.status_code == 200, f"mesh_memory failed: {resp.status_code}"
            data = resp.json()
            assert data["server_id"] == "test-srv", f"server_id mismatch: {data}"

            resp = await client.get("/signal_scores/test-srv")
            assert resp.status_code == 200, f"signal_scores failed: {resp.status_code}"
            scores_data = resp.json()
            assert scores_data["scores"][0]["score"] == 0.85

            resp = await client.get("/score_disputes")
            assert resp.status_code == 200, f"score_disputes failed: {resp.status_code}"

        await _test_engine.dispose()
        print("PASS")

    asyncio.run(run_self_test())