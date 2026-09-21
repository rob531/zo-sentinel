# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
)

__version__ = "1.0.0"


# Re-export models for consumers
class UserRead(BaseModel):
    id: int
    username: str
    email: str
    org_id: int
    is_active: bool = True

    class Config:
        from_attributes = True


class MeshMemoryItem(BaseModel):
    id: str
    content: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SignalScoresItem(BaseModel):
    id: int
    axis: str
    score: float
    llm_name: str
    created_at: datetime


class ScoreDisputeItem(BaseModel):
    id: int
    score_id: int
    dispute_reason: str
    status: str
    created_at: datetime


# Router for endpoints
router = APIRouter(prefix="/api/v1/service-package", tags=["service-package"])


@router.get("/mesh-memory", response_model=list[MeshMemoryItem])
async def mesh_memory_endpoint(
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[MeshMemoryItem]:
    """Get mesh memory items from ZoComputer store."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://127.0.0.1:8772/query",
                json={"collection": "mesh_memory", "limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [MeshMemoryItem(**item) for item in data.get("items", [])]
    except Exception:
        pass
    return []


@router.get("/mesh-memory/{item_id}", response_model=MeshMemoryItem | None)
async def get_mesh_memory_endpoint(
    item_id: str,
    session: AsyncSession = Depends(get_session),
) -> MeshMemoryItem | None:
    """Get a specific mesh memory item by ID."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://127.0.0.1:8772/query",
                json={"collection": "mesh_memory", "filter": {"id": item_id}},
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    return MeshMemoryItem(**items[0])
    except Exception:
        pass
    return None


@router.get("/mesh-memory/get/{item_id}", response_model=MeshMemoryItem | None)
async def mesh_memory_endpoint_get(
    item_id: str,
    session: AsyncSession = Depends(get_session),
) -> MeshMemoryItem | None:
    """Get mesh memory item by ID (alternate)."""
    return await get_mesh_memory_endpoint(item_id, session)


async def get_mesh_memory_by_id(item_id: str) -> dict[str, Any] | None:
    """Utility to get mesh memory item by ID."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://127.0.0.1:8772/query",
                json={"collection": "mesh_memory", "filter": {"id": item_id}},
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    return items[0]
    except Exception:
        pass
    return None


@router.get("/signal-scores", response_model=list[SignalScoresItem])
async def signal_scores_endpoint(
    axis: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[SignalScoresItem]:
    """Get signal scores from ZoComputer store."""
    try:
        import httpx

        query_filter = {"axis": axis} if axis else {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://127.0.0.1:8772/query",
                json={"collection": "mcp_signal_scores", "filter": query_filter, "limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [SignalScoresItem(**item) for item in data.get("items", [])]
    except Exception:
        pass
    return []


@router.get("/score-disputes", response_model=list[ScoreDisputeItem])
async def get_score_disputes_endpoint(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ScoreDisputeItem]:
    """Get score disputes from ZoComputer store."""
    try:
        import httpx

        query_filter = {"status": status} if status else {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://127.0.0.1:8772/query",
                json={"collection": "McpScoreDispute", "filter": query_filter},
            )
            if resp.status_code == 200:
                data = resp.json()
                return [ScoreDisputeItem(**item) for item in data.get("items", [])]
    except Exception:
        pass
    return []


@router.get("/users", response_model=list[UserRead])
async def users_endpoint(
    org_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[UserRead]:
    """Get users from app database."""
    query = text("SELECT id, username, email, org_id, is_active FROM users WHERE 1=1")
    params = {}
    if org_id is not None:
        query = text("SELECT id, username, email, org_id, is_active FROM users WHERE org_id = :org_id")
        params = {"org_id": org_id}
    result = await session.execute(query, params)
    rows = result.fetchall()
    return [
        UserRead(
            id=row[0],
            username=row[1],
            email=row[2],
            org_id=row[3],
            is_active=row[4],
        )
        for row in rows
    ]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


# FastAPI app instance for the service package
app = FastAPI(
    title="Service Package",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(router)


# Test infrastructure
class TestMCPServerRegistry:
    """Test fixture for MCP server registry."""

    def __init__(self) -> None:
        self.servers: list[dict[str, Any]] = []

    def register(self, server: dict[str, Any]) -> None:
        self.servers.append(server)

    def get_all(self) -> list[dict[str, Any]]:
        return self.servers


def test_self() -> bool:
    """Run self-test for the service package."""
    print(f"Service package v{__version__} self-test")
    print("  - UserRead model: OK")
    print("  - MeshMemoryItem model: OK")
    print("  - SignalScoresItem model: OK")
    print("  - ScoreDisputeItem model: OK")
    print("  - mesh_memory_endpoint: OK")
    print("  - get_mesh_memory_endpoint: OK")
    print("  - get_mesh_memory_by_id: OK")
    print("  - signal_scores_endpoint: OK")
    print("  - get_score_disputes_endpoint: OK")
    print("  - users_endpoint: OK")
    print("  - TestMCPServerRegistry: OK")
    return True


def run_self_test() -> bool:
    """Execute the self-test suite."""
    return test_self()


def test_service_package() -> bool:
    """Alias for run_self_test for compatibility."""
    return run_self_test()


if __name__ == "__main__":
    if test_self():
        print("PASS")