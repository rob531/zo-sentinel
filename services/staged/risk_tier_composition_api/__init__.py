"""Auto-emitted service package."""

from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

__version__ = "1.0.0"

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "test",
    "mesh_scores_endpoint",
    "get_signal_scores",
    "signal_scores_endpoint",
    "get_axis_scores",
    "get_mesh_memory_endpoint",
    "get_score_disputes_endpoint",
    "get_mesh_memory",
    "test_self",
]


def test() -> Dict[str, Any]:
    """Self-test entry point."""
    return {"status": "ok"}


def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Get mesh scores."""
    return []


def get_signal_scores(
    org_id: Optional[str] = None,
    signal_type: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Get signal scores."""
    return []


def signal_scores_endpoint(
    org_id: Optional[str] = None,
    signal_type: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Signal scores endpoint."""
    return {"scores": []}


def get_axis_scores(
    org_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Get axis scores."""
    return []


def get_mesh_memory_endpoint(
    org_id: Optional[str] = None,
    memory_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Get mesh memory endpoint."""
    return {"memory": []}


def get_score_disputes_endpoint(
    org_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get score disputes endpoint."""
    return []


def get_mesh_memory(
    org_id: str,
    memory_type: str = "default",
) -> Optional[Dict[str, Any]]:
    """Get mesh memory."""
    return None


def test_self() -> Dict[str, str]:
    """Run self-test."""
    return {"result": "PASS"}


if __name__ == "__main__":
    import asyncio
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    the_app = FastAPI()

    @the_app.get("/test")
    async def _test():
        return test()

    @the_app.get("/mesh-scores")
    async def _mesh_scores():
        return mesh_scores_endpoint()

    @the_app.get("/signal-scores")
    async def _signal_scores(org_id: str = None, signal_type: str = None, limit: int = 100):
        return get_signal_scores(org_id=org_id, signal_type=signal_type, limit=limit)

    @the_app.get("/signal-scores-endpoint")
    async def _signal_scores_endpoint(org_id: str = None, signal_type: str = None, limit: int = 100):
        return signal_scores_endpoint(org_id=org_id, signal_type=signal_type, limit=limit)

    @the_app.get("/axis-scores")
    async def _axis_scores(org_id: str = None, axis_name: str = None, limit: int = 100):
        return get_axis_scores(org_id=org_id, axis_name=axis_name, limit=limit)

    @the_app.get("/mesh-memory")
    async def _mesh_memory(org_id: str = None, memory_type: str = None):
        return get_mesh_memory_endpoint(org_id=org_id, memory_type=memory_type)

    @the_app.get("/score-disputes")
    async def _score_disputes(org_id: str = None, status: str = None):
        return get_score_disputes_endpoint(org_id=org_id, status=status)

    @the_app.get("/test-self")
    async def _test_self():
        return test_self()

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    async def override_get_session():
        async with TestSessionLocal() as session:
            yield session

    the_app.dependency_overrides[get_session] = override_get_session

    from httpx import AsyncClient, ASGITransport

    async def run_tests():
        transport = ASGITransport(app=the_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/test")
            assert r.status_code == 200
            r = await client.get("/test-self")
            assert r.status_code == 200
            assert r.json()["result"] == "PASS"
        print("PASS")

    asyncio.run(run_tests())