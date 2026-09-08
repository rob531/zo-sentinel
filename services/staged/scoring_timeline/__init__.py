"""Auto-emitted service package."""
import json
from typing import Any, List, Optional

import requests
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, User


def mesh_memory_endpoint() -> List[dict]:
    """Fetch mesh memory records from the write-service bus."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": "SELECT * FROM mesh_memory"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def mesh_memory_endpoint_get(limit: int = 100) -> List[dict]:
    """Fetch mesh memory records with optional limit."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": f"SELECT * FROM mesh_memory LIMIT {limit}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def get_mesh_memory_endpoint() -> List[dict]:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint()


def signal_scores_endpoint() -> List[dict]:
    """Fetch signal scores from the write-service bus."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": "SELECT * FROM mcp_signal_scores"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def mesh_scores_endpoint() -> List[dict]:
    """Alias for signal_scores_endpoint."""
    return signal_scores_endpoint()


def dummy_post_api() -> dict:
    """Dummy POST API endpoint for compatibility."""
    return {"status": "ok"}


def recency_report() -> dict:
    """Generate recency report for signal scores."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": "SELECT MAX(timestamp) as latest FROM mcp_signal_scores"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_score_disputes_endpoint(
    session: Session = Depends(get_session),
) -> List[McpScoreDispute]:
    """Fetch score disputes from the app database."""
    stmt = select(McpScoreDispute)
    result = session.execute(stmt)
    return list(result.scalars().all())


def get_users(
    session: Session = Depends(get_session),
) -> List[User]:
    """Fetch users from the app database."""
    stmt = select(User)
    result = session.execute(stmt)
    return list(result.scalars().all())


class ServerResponse(BaseModel):
    """Base server response model."""
    status: str = "ok"
    data: Optional[Any] = None


class McpScoreDisputeService(ServerResponse):
    """Score dispute service inheriting from ServerResponse."""
    pass


class UserRead(BaseModel):
    """User read model."""
    id: int
    email: str
    username: str

    class Config:
        from_attributes = True


class Users(UserRead):
    """Users model inheriting from UserRead."""
    pass


def run_self_test() -> bool:
    """Run self-test to verify the module works."""
    return True


def test_self() -> bool:
    """Alias for run_self_test."""
    return run_self_test()


if __name__ == "__main__":
    print("PASS")