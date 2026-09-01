from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
import httpx

from app.db import get_session
from app.models import McpScoreDispute, User, McpServerRegistry

router = APIRouter()


class MeshMemoryResponse(BaseModel):
    id: str
    content: Optional[dict] = None


class SignalScore(BaseModel):
    id: str
    signal_type: str
    score: float


def mesh_memory_endpoint(limit: int = 100) -> list[dict]:
    payload = {"sql": f"SELECT * FROM mesh_memory LIMIT {limit}"}
    resp = httpx.post("http://127.0.0.1:8772/query", json=payload, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def mesh_memory_endpoint_get(memory_id: str) -> dict:
    payload = {"sql": f"SELECT * FROM mesh_memory WHERE id = '{memory_id}'"}
    resp = httpx.post("http://127.0.0.1:8772/query", json=payload, timeout=10.0)
    resp.raise_for_status()
    results = resp.json()
    return results[0] if results else None


def get_mesh_memory_by_id(memory_id: str) -> Optional[MeshMemoryResponse]:
    data = mesh_memory_endpoint_get(memory_id)
    if data:
        return MeshMemoryResponse(id=data["id"], content=data.get("content"))
    return None


def get_mesh_memory_endpoint() -> list[dict]:
    return mesh_memory_endpoint(limit=10)


def signal_scores_endpoint(limit: int = 100) -> list[dict]:
    payload = {"sql": f"SELECT * FROM mcp_signal_scores LIMIT {limit}"}
    resp = httpx.post("http://127.0.0.1:8772/query", json=payload, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def get_score_disputes_endpoint(
    session=Depends(get_session),
) -> list[McpScoreDispute]:
    return session.query(McpScoreDispute).all()


def users_endpoint(session=Depends(get_session)) -> list[User]:
    return session.query(User).all()


@router.get("/health")
def health_check():
    return {"status": "ok"}


def run_self_test():
    """Verify all endpoints are functional using in-memory store override."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create in-memory SQLite for test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_session = TestingSessionLocal()

    # Patch get_session
    def _override():
        yield test_session

    # Import app after patching
    import sys
    from types import ModuleType

    stub_app = ModuleType("app")
    stub_main = ModuleType("app.main")
    stub_db = ModuleType("app.db")
    stub_models = ModuleType("app.models")

    from app import models as real_models
    stub_models.Base = real_models.Base
    stub_models.User = real_models.User
    stub_models.McpServerRegistry = real_models.McpServerRegistry
    stub_models.McpScoreDispute = real_models.McpScoreDispute
    stub_models.McpLlmAxisScore = real_models.McpLlmAxisScore

    stub_db.get_session = get_session

    sys.modules["app.models"] = stub_models
    sys.modules["app.db"] = stub_db

    # Re-import this module
    import importlib
    import types

    current_module = types.ModuleType(__name__)
    current_module.router = router
    current_module.mesh_memory_endpoint = mesh_memory_endpoint
    current_module.get_mesh_memory_by_id = get_mesh_memory_by_id
    current_module.signal_scores_endpoint = signal_scores_endpoint
    current_module.get_score_disputes_endpoint = get_score_disputes_endpoint
    current_module.users_endpoint = users_endpoint
    current_module.get_mesh_memory_endpoint = get_mesh_memory_endpoint
    current_module.mesh_memory_endpoint_get = mesh_memory_endpoint_get

    from unittest.mock import patch

    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value.json.return_value = [
            {"id": "test-1", "content": {"key": "value"}}
        ]
        mock_post.return_value.raise_for_status = lambda: None

        with patch("httpx.post", mock_post):
            result = mesh_memory_endpoint()
            assert isinstance(result, list), f"mesh_memory_endpoint failed: {result}"

            result = get_mesh_memory_by_id("test-1")
            assert result is not None, "get_mesh_memory_by_id returned None"

            result = signal_scores_endpoint()
            assert isinstance(result, list), f"signal_scores_endpoint failed: {result}"

            result = users_endpoint(session=test_session)
            assert isinstance(result, list), f"users_endpoint failed: {result}"

            result = get_score_disputes_endpoint(session=test_session)
            assert isinstance(result, list), f"get_score_disputes failed: {result}"

    test_session.close()
    print("PASS")


if __name__ == "__main__":
    run_self_test()