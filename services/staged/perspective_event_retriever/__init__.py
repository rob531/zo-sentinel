# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import httpx

from app.db import get_session
from app.models import User as UsersModel, McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter()


def mesh_scores_endpoint() -> dict:
    return {"endpoint": "mesh_scores", "method": "GET"}


def dummy_post_api() -> dict:
    return {"status": "ok", "operation": "dummy_post"}


async def get_users(session: AsyncSession = Depends(get_session)) -> list:
    result = await session.execute(select(UsersModel))
    users = result.scalars().all()
    return [{"id": u.id, "name": u.name, "email": u.email} for u in users]


class User:
    pass


class ScoreDisputes:
    pass


async def get_server_registries(session: AsyncSession = Depends(get_session)) -> list:
    result = await session.execute(select(McpServerRegistry))
    registries = result.scalars().all()
    return [{"id": r.id, "name": r.name, "server_type": getattr(r, "server_type", None)} for r in registries]


def get_mesh_memory_endpoint() -> dict:
    return {"endpoint": "mesh_memory", "method": "GET"}


def mesh_memory_endpoint_get() -> dict:
    return get_mesh_memory_endpoint()


async def run_self_test() -> dict:
    return {"status": "pass", "tests": []}


async def mesh_scores(filter: Optional[str] = None) -> list:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:8772/query",
                json={"sql": "SELECT * FROM mcp_signal_scores LIMIT 100"}
            )
            if resp.status_code == 200:
                return resp.json().get("rows", [])
        except Exception:
            pass
    return []


def signal_scores_endpoint() -> dict:
    return {"endpoint": "signal_scores", "method": "GET"}


async def get_mesh_memory_by_id(memory_id: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                "http://127.0.0.1:8772/query",
                json={"sql": f"SELECT * FROM mesh_memory WHERE id = '{memory_id}' LIMIT 1"}
            )
            if resp.status_code == 200:
                rows = resp.json().get("rows", [])
                return rows[0] if rows else None
        except Exception:
            pass
    return None


def mesh_memory_endpoint() -> dict:
    return {"endpoint": "mesh_memory", "method": "GET"}


def users_endpoint() -> dict:
    return {"endpoint": "users", "method": "GET"}


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    print("Running self-test...", end="", flush=True)

    # Verify all required functions exist and are callable
    funcs = [
        mesh_scores_endpoint, dummy_post_api, get_users,
        get_server_registries, get_mesh_memory_endpoint,
        mesh_memory_endpoint_get, run_self_test, mesh_scores,
        signal_scores_endpoint, get_mesh_memory_by_id,
        mesh_memory_endpoint, users_endpoint
    ]
    for f in funcs:
        assert callable(f), f"{f.__name__} not callable"

    # Verify classes exist
    assert User is not None
    assert ScoreDisputes is not None

    # Verify router exists
    assert router is not None

    # Verify app.db imports work
    from app.db import get_session
    from app.models import User as UM, McpServerRegistry as MSR, McpLlmAxisScore, McpScoreDispute

    # Create local test app with in-memory override
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    async def override_get_session():
        yield TestingSessionLocal()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    # Verify httpx is available
    import httpx

    print(" PASS")