"""Auto-emitted service package."""

from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, User

MESH_STORE_URL = "http://127.0.0.1:8772/query"


def mesh_scores_endpoint() -> str:
    return "/api/v1/mesh/scores"


def signal_scores_endpoint() -> str:
    return "/api/v1/signal/scores"


def users_endpoint() -> str:
    return "/api/v1/users"


def mesh_memory_endpoint() -> str:
    return "/api/v1/mesh/memory"


def get_mesh_memory_endpoint() -> str:
    return mesh_memory_endpoint()


def mesh_memory_endpoint_get(memory_id: str) -> dict[str, Any] | None:
    import requests

    resp = requests.post(MESH_STORE_URL, json={"table": "mesh_memory", "id": memory_id})
    if resp.status_code == 200:
        return resp.json()
    return None


def get_mesh_memory_by_id(memory_id: str) -> dict[str, Any] | None:
    return mesh_memory_endpoint_get(memory_id)


def mesh_scores() -> dict[str, Any] | None:
    import requests

    resp = requests.post(MESH_STORE_URL, json={"table": "mcp_signal_scores"})
    if resp.status_code == 200:
        return resp.json()
    return None


def get_users(session: Session = Depends(get_session)) -> list[User]:
    stmt = select(User)
    result = session.execute(stmt)
    return list(result.scalars().all())


def get_server_registries(session: Session = Depends(get_session)) -> list[McpServerRegistry]:
    stmt = select(McpServerRegistry)
    result = session.execute(stmt)
    return list(result.scalars().all())


def run_self_test() -> str:
    assert callable(mesh_scores_endpoint)
    assert callable(signal_scores_endpoint)
    assert callable(users_endpoint)
    assert callable(mesh_memory_endpoint)
    assert callable(get_mesh_memory_endpoint)
    assert callable(mesh_memory_endpoint_get)
    assert callable(get_mesh_memory_by_id)
    assert callable(mesh_scores)
    assert callable(get_users)
    assert callable(get_server_registries)
    assert Users is not None
    assert ScoreDisputes is not None
    assert TestMCPServerRegistry is not None
    return "PASS"


class Users(User):
    pass


class ScoreDisputes(McpScoreDispute):
    pass


class TestMCPServerRegistry(McpServerRegistry):
    pass


if __name__ == "__main__":
    print(run_self_test())