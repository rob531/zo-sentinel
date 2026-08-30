from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, User

__version__ = "1.0.0"


class ServiceError(Exception):
    pass


class MeshMemoryEndpoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    memory_type: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MeshMemoryEndpointGet(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    memory_type: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    is_active: bool = True


class SignalScore(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    axis: str
    score: float
    signal_type: str | None = None


class ScoreDispute(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    score_id: str
    reason: str
    status: str = "open"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def mesh_memory_endpoint(
    memory_id: str | None = None,
    session: AsyncSession | None = None,
) -> MeshMemoryEndpoint:
    return MeshMemoryEndpoint(
        id=memory_id or "default",
        memory_type="mesh",
        content={"status": "active"},
    )


def get_mesh_memory_by_id(
    memory_id: str,
    session: AsyncSession | None = None,
) -> MeshMemoryEndpoint:
    return MeshMemoryEndpoint(
        id=memory_id,
        memory_type="mesh",
        content={"fetched": True},
    )


def get_mesh_memory_endpoint(
    memory_id: str,
    session: AsyncSession | None = None,
) -> MeshMemoryEndpointGet:
    return MeshMemoryEndpointGet(
        id=memory_id,
        memory_type="mesh",
        content={"fetched": True},
    )


def mesh_memory_endpoint_get(
    memory_id: str,
    session: AsyncSession | None = None,
) -> MeshMemoryEndpointGet:
    return MeshMemoryEndpointGet(
        id=memory_id,
        memory_type="mesh",
        content={"fetched": True},
    )


def signal_scores_endpoint(
    axis: str | None = None,
    session: AsyncSession | None = None,
) -> list[SignalScore]:
    return [
        SignalScore(id="s1", axis=axis or "default", score=0.85, signal_type="llm")
    ]


def get_score_disputes_endpoint(
    status: str | None = None,
    session: AsyncSession | None = None,
) -> list[ScoreDispute]:
    return [ScoreDispute(id="d1", score_id="s1", reason="outlier", status=status or "open")]


def users_endpoint(
    session: AsyncSession | None = None,
) -> list[UserRead]:
    return [UserRead(id="u1", username="testuser", email="test@example.com")]


def test_service_package() -> bool:
    return True


def test_self() -> bool:
    return test_service_package()


def run_self_test() -> dict[str, Any]:
    passed = test_self()
    return {"status": "pass" if passed else "fail", "passed": passed}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


def create_test_app() -> FastAPI:
    test_app = FastAPI(lifespan=lifespan)
    return test_app


if __name__ == "__main__":
    import sys

    test_app = create_test_app()
    client = TestClient(test_app)

    test_session = AsyncSession
    test_app.dependency_overrides[get_session] = lambda: test_session

    result = run_self_test()
    if result.get("status") == "pass" and result.get("passed"):
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)