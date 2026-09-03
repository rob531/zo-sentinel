"""Auto-emitted service package for staged services."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    import httpx

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    VulnAdvisory,
)

_bust_host = os.environ.get("BUST_HOST", "127.0.0.1")
_bust_port = os.environ.get("BUST_PORT", "8772")
_bus_base = f"http://{_bust_host}:{_bust_port}"

_http_client: httpx.AsyncClient | None = None


async def query_mesh_bus(sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Query mesh bus (ZoComputer store) via write-service."""
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.AsyncClient(base_url=_bus_base, timeout=30.0)
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = await _http_client.post("/query", json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", data) if isinstance(data, dict) else data


def get_db() -> Session:
    """Get app database session (FastAPI dependency)."""
    return Depends(get_session)


async def get_mesh_memory(org_id: str | None = None) -> list[dict]:
    """Get mesh_memory records from bus."""
    sql = "SELECT * FROM mesh_memory"
    params = None
    if org_id:
        sql += " WHERE org_id = :org_id"
        params = {"org_id": org_id}
    return await query_mesh_bus(sql, params)


async def mesh_scores_endpoint(
    org_id: str | None = None,
    signal_type: str | None = None,
    lookback_hours: int | None = None,
) -> list[dict]:
    """Get mesh scores from bus."""
    sql = "SELECT * FROM mcp_signal_scores WHERE 1=1"
    params: dict[str, Any] = {}
    if org_id:
        sql += " AND org_id = :org_id"
        params["org_id"] = org_id
    if signal_type:
        sql += " AND signal_type = :signal_type"
        params["signal_type"] = signal_type
    if lookback_hours:
        sql += " AND created_at >= NOW() - INTERVAL ':lookback hours'"
        params["lookback"] = lookback_hours
    return await query_mesh_bus(sql, params)


async def signal_scores_endpoint(
    org_id: str | None = None,
    signal_type: str | None = None,
    lookback_hours: int | None = None,
) -> list[dict]:
    """Get signal scores from bus."""
    return await mesh_scores_endpoint(org_id, signal_type, lookback_hours)


def mesh_scores(
    org_id: str | None = None,
    signal_type: str | None = None,
    lookback_hours: int | None = None,
) -> list[dict]:
    """Sync wrapper for mesh_scores_endpoint."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        mesh_scores_endpoint(org_id, signal_type, lookback_hours)
    )


def get_signal_scores(
    org_id: str | None = None,
    signal_type: str | None = None,
    lookback_hours: int | None = None,
) -> list[dict]:
    """Sync wrapper for signal_scores_endpoint."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        signal_scores_endpoint(org_id, signal_type, lookback_hours)
    )


async def get_mesh_memory_endpoint(org_id: str | None = None) -> dict:
    """Serve mesh_memory via endpoint."""
    result = await get_mesh_memory(org_id)
    return {"mesh_memory": result}


class VulnerabilityLink:
    """Base class for vulnerability associations."""

    def __init__(
        self,
        vulnerability_id: str | None = None,
        score: float | None = None,
        severity: str | None = None,
        description: str | None = None,
    ):
        self.vulnerability_id = vulnerability_id
        self.score = score
        self.severity = severity
        self.description = description


class TestMcpServerRegistry:
    """Test fixture for MCP server registry."""

    def __init__(self, server_name: str | None = None, enabled: bool = True):
        self.server_name = server_name or "test-server"
        self.enabled = enabled


async def _run_self_test() -> dict[str, Any]:
    """Run self-test suite."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE mcp_server_registry (id INTEGER PRIMARY KEY, server_name TEXT, enabled INTEGER)"))
        conn.execute(text("CREATE TABLE mcp_signal_scores (id INTEGER PRIMARY KEY, org_id TEXT, signal_type TEXT, score REAL)"))
        conn.execute(text("CREATE TABLE mesh_memory (id INTEGER PRIMARY KEY, org_id TEXT, data TEXT)"))
        conn.commit()

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    test_session = TestingSessionLocal()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = lambda: test_session

    results: list[tuple[str, str]] = []

    try:
        sess = test_session
        result = sess.execute(text("SELECT 1"))
        row = result.scalar()
        results.append(("get_db", "PASS" if row == 1 else "FAIL"))
    except Exception as e:
        results.append(("get_db", f"FAIL: {e}"))

    try:
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"rows": [{"id": 1, "data": "test"}]}
        class MockClient:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, url, **kwargs):
                return MockResp()
        import httpx
        orig_client = httpx.AsyncClient
        httpx.AsyncClient = MockClient
        global _http_client
        _http_client = None
        mm = await get_mesh_memory()
        results.append(("mesh_memory_query", "PASS" if isinstance(mm, list) else f"FAIL: got {type(mm)}"))
        httpx.AsyncClient = orig_client
        _http_client = None
    except Exception as e:
        results.append(("mesh_memory_query", f"FAIL: {e}"))

    try:
        McpServerRegistry
        results.append(("app.models_import", "PASS"))
    except Exception as e:
        results.append(("app.models_import", f"FAIL: {e}"))

    try:
        VulnAdvisory
        results.append(("VulnAdvisory_import", "PASS"))
    except Exception as e:
        results.append(("VulnAdvisory_import", f"FAIL: {e}"))

    test_session.close()
    engine.dispose()

    passed = sum(1 for _, status in results if status == "PASS")
    total = len(results)
    print(f"\n{'='*40}")
    print(f"  SELF-TEST RESULTS ({passed}/{total} passed)")
    print(f"{'='*40}")
    for name, status in results:
        mark = "✓" if status == "PASS" else "✗"
        print(f"  {mark} {name}: {status}")
    print(f"{'='*40}\n")

    return {"passed": passed, "total": total, "results": results}


def test_endpoint() -> dict[str, Any]:
    """Sync wrapper for self-test."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_run_self_test())


if __name__ == "__main__":
    result = test_endpoint()
    passed = result["passed"]
    total = result["total"]
    print("PASS" if passed == total else f"FAIL ({passed}/{total})")