from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute, User


# --- Bus client ---
BUS_URL = "http://127.0.0.1:8772"


async def _bus_query(sql: str, params: Optional[dict] = None):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BUS_URL}/query",
            json={"sql": sql, "params": params or {}}
        )
        resp.raise_for_status()
        return resp.json()


# --- Pydantic schemas ---
class ServerResponse(BaseModel):
    id: str
    name: str
    registry: str
    metadata: Optional[dict] = None

    class Config:
        from_attributes = True


class MeshMemoryRecord(BaseModel):
    id: int
    memory_type: Optional[str] = None
    payload: Optional[dict] = None
    created_at: Optional[str] = None


class SignalScoreRecord(BaseModel):
    id: int
    server_id: Optional[str] = None
    signal_type: Optional[str] = None
    score: Optional[float] = None
    metadata: Optional[dict] = None


# --- Exported router ---
router = APIRouter(prefix="/service-package", tags=["service-package"])


# --- mesh_memory_endpoint (POST) ---
@router.post("/mesh-memory", response_model=List[MeshMemoryRecord])
async def mesh_memory_endpoint(
    memory_type: Optional[str] = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
):
    sql = "SELECT id, memory_type, payload, created_at FROM mesh_memory WHERE 1=1"
    params = {}
    if memory_type:
        sql += " AND memory_type = %(memory_type)s"
        params["memory_type"] = memory_type
    sql += " ORDER BY created_at DESC LIMIT %(limit)s"
    params["limit"] = limit
    return await _bus_query(sql, params)


# --- mesh_memory_endpoint_get (GET by id) ---
@router.get("/mesh-memory/{record_id}", response_model=MeshMemoryRecord)
async def mesh_memory_endpoint_get(
    record_id: int,
    session: AsyncSession = Depends(get_session),
):
    sql = "SELECT id, memory_type, payload, created_at FROM mesh_memory WHERE id = %(id)s"
    rows = await _bus_query(sql, {"id": record_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Mesh memory record not found")
    return rows[0]


# --- get_mesh_memory_by_id ---
async def get_mesh_memory_by_id(record_id: int) -> Optional[MeshMemoryRecord]:
    sql = "SELECT id, memory_type, payload, created_at FROM mesh_memory WHERE id = %(id)s"
    rows = await _bus_query(sql, {"id": record_id})
    if not rows:
        return None
    return MeshMemoryRecord(**rows[0])


# --- get_mesh_memory_endpoint ---
async def get_mesh_memory_endpoint(
    memory_type: Optional[str] = None,
    limit: int = 100,
) -> List[MeshMemoryRecord]:
    sql = "SELECT id, memory_type, payload, created_at FROM mesh_memory WHERE 1=1"
    params = {}
    if memory_type:
        sql += " AND memory_type = %(memory_type)s"
        params["memory_type"] = memory_type
    sql += " ORDER BY created_at DESC LIMIT %(limit)s"
    params["limit"] = limit
    return await _bus_query(sql, params)


# --- _get_mesh_memory_impl ---
async def _get_mesh_memory_impl(
    memory_type: Optional[str] = None,
    limit: int = 50,
) -> List[MeshMemoryRecord]:
    return await get_mesh_memory_endpoint(memory_type=memory_type, limit=limit)


# --- signal_scores_endpoint ---
@router.get("/signal-scores", response_model=List[SignalScoreRecord])
async def signal_scores_endpoint(
    server_id: Optional[str] = None,
    signal_type: Optional[str] = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
):
    sql = "SELECT id, server_id, signal_type, score, metadata FROM mcp_signal_scores WHERE 1=1"
    params = {}
    if server_id:
        sql += " AND server_id = %(server_id)s"
        params["server_id"] = server_id
    if signal_type:
        sql += " AND signal_type = %(signal_type)s"
        params["signal_type"] = signal_type
    sql += " ORDER BY id DESC LIMIT %(limit)s"
    params["limit"] = limit
    return await _bus_query(sql, params)


# --- get_server_registries ---
async def get_server_registries(
    session: AsyncSession,
    registry_name: Optional[str] = None,
) -> List[ServerResponse]:
    query = select(McpServerRegistry)
    if registry_name:
        query = query.where(McpServerRegistry.registry == registry_name)
    result = await session.execute(query)
    rows = result.scalars().all()
    return [
        ServerResponse(
            id=str(r.id),
            name=r.name,
            registry=r.registry,
            metadata=getattr(r, "metadata", None),
        )
        for r in rows
    ]


# --- McpScoreDisputeService ---
class McpScoreDisputeService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_disputes(self, server_id: Optional[str] = None) -> List[dict]:
        query = select(McpScoreDispute)
        if server_id:
            query = query.where(McpScoreDispute.server_id == server_id)
        result = await self.session.execute(query)
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "server_id": r.server_id,
                "axis": getattr(r, "axis", None),
                "status": getattr(r, "status", None),
            }
            for r in rows
        ]


# --- UserRead ---
class UserRead(BaseModel):
    id: int
    username: str
    email: Optional[str] = None

    class Config:
        from_attributes = True


# --- self-test ---
if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import asyncio

    # In-memory test DB
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def setup_test_db():
        from app.models import Base
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def run_tests():
        await setup_test_db()
        TestSession = async_sessionmaker(test_engine, expire_on_commit=False)

        async with TestSession() as test_session:
            # Test get_server_registries
            registries = await get_server_registries(test_session)
            assert isinstance(registries, list)

            # Test McpScoreDisputeService
            svc = McpScoreDisputeService(test_session)
            disputes = await svc.get_disputes()
            assert isinstance(disputes, list)

            # Test UserRead model
            user = UserRead(id=1, username="test", email="test@example.com")
            assert user.username == "test"

            # Test ServerResponse model
            resp = ServerResponse(id="1", name="test-server", registry="primary")
            assert resp.name == "test-server"

            # Test MeshMemoryRecord model
            record = MeshMemoryRecord(id=1, memory_type="test", payload={})
            assert record.id == 1

            # Test SignalScoreRecord model
            score = SignalScoreRecord(id=1, score=0.95)
            assert score.score == 0.95

        print("PASS")

    asyncio.run(run_tests())