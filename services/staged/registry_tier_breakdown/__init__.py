# Auto-emitted service package.
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute, VulnAdvisory


# ── Pydantic contracts ──────────────────────────────────────────────────────

class MeshMemoryItem(BaseModel):
    key: str
    value: Any
    timestamp: datetime | None = None
    tags: list[str] = Field(default_factory=list)


class SignalScoreItem(BaseModel):
    server_id: str
    axis: str
    score: float
    confidence: float | None = None
    timestamp: datetime | None = None


class ScoreDisputeItem(BaseModel):
    dispute_id: str
    server_id: str
    axis: str
    claimed_score: float
    status: str
    created_at: datetime | None = None


class ServerResponse(BaseModel):
    server_id: str
    name: str
    risk_tier: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserRead(BaseModel):
    user_id: str
    name: str
    email: str | None = None


class McpScoreDisputeService:
    """Service layer for score disputes."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_open_disputes(self, server_id: str | None = None) -> list[dict[str, Any]]:
        query = select(McpScoreDispute).where(McpScoreDispute.status == "open")
        if server_id:
            query = query.where(McpScoreDispute.server_id == server_id)
        result = await self.session.execute(query)
        disputes = result.scalars().all()
        return [
            {
                "dispute_id": str(d.id),
                "server_id": d.server_id,
                "axis": getattr(d, "axis", "unknown"),
                "claimed_score": getattr(d, "claimed_score", 0.0),
                "status": d.status,
                "created_at": getattr(d, "created_at", None),
            }
            for d in disputes
        ]

    async def create_dispute(self, server_id: str, axis: str, claimed_score: float) -> dict[str, Any]:
        dispute = McpScoreDispute(
            server_id=server_id,
            axis=axis,
            claimed_score=claimed_score,
            status="open",
        )
        self.session.add(dispute)
        await self.session.commit()
        await self.session.refresh(dispute)
        return {
            "dispute_id": str(dispute.id),
            "server_id": dispute.server_id,
            "axis": dispute.axis,
            "claimed_score": dispute.claimed_score,
            "status": dispute.status,
        }


# ── Mesh Memory helpers (write-service bus) ─────────────────────────────────

MESH_BUS = "http://127.0.0.1:8772"


async def _mesh_query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    import httpx

    payload = {"sql": sql, "params": params or {}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{MESH_BUS}/query", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])


async def mesh_memory_endpoint(key: str | None = None) -> list[MeshMemoryItem]:
    """Fetch mesh_memory rows, optionally filtered by key."""
    if key:
        sql = "SELECT key, value, timestamp, tags FROM mesh_memory WHERE key = :key LIMIT 100"
        rows = await _mesh_query(sql, {"key": key})
    else:
        sql = "SELECT key, value, timestamp, tags FROM mesh_memory LIMIT 200"
        rows = await _mesh_query(sql)
    return [
        MeshMemoryItem(
            key=r.get("key", ""),
            value=r.get("value"),
            timestamp=r.get("timestamp"),
            tags=json.loads(r["tags"]) if isinstance(r.get("tags"), str) else r.get("tags", []),
        )
        for r in rows
    ]


async def mesh_memory_endpoint_get(keys: list[str]) -> list[MeshMemoryItem]:
    """Fetch multiple mesh_memory keys at once."""
    if not keys:
        return []
    placeholders = ", ".join(f":k{i}" for i in range(len(keys)))
    sql = f"SELECT key, value, timestamp, tags FROM mesh_memory WHERE key IN ({placeholders})"
    params = {f"k{i}": k for i, k in enumerate(keys)}
    rows = await _mesh_query(sql, params)
    return [
        MeshMemoryItem(
            key=r.get("key", ""),
            value=r.get("value"),
            timestamp=r.get("timestamp"),
            tags=json.loads(r["tags"]) if isinstance(r.get("tags"), str) else r.get("tags", []),
        )
        for r in rows
    ]


def get_mesh_memory_endpoint() -> list[MeshMemoryItem]:
    """Sync wrapper for mesh_memory endpoint."""
    return asyncio.run(mesh_memory_endpoint())


async def signal_scores_endpoint(
    server_id: str | None = None,
    axis: str | None = None,
) -> list[SignalScoreItem]:
    """Fetch mcp_signal_scores rows."""
    conditions = []
    params: dict[str, Any] = {}
    if server_id:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if axis:
        conditions.append("axis = :axis")
        params["axis"] = axis
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT server_id, axis, score, confidence, timestamp FROM mcp_signal_scores{where} ORDER BY timestamp DESC LIMIT 200"
    rows = await _mesh_query(sql, params)
    return [
        SignalScoreItem(
            server_id=r.get("server_id", ""),
            axis=r.get("axis", ""),
            score=float(r.get("score", 0)),
            confidence=float(r.get("confidence", 0)) if r.get("confidence") else None,
            timestamp=r.get("timestamp"),
        )
        for r in rows
    ]


async def critical_risk_servers_endpoint(limit: int = 50) -> list[ServerResponse]:
    """Fetch servers with critical risk tier."""
    sql = "SELECT server_id, name, risk_tier, status, metadata FROM mcp_server_registry WHERE risk_tier = 'critical' ORDER BY name LIMIT :limit"
    rows = await _mesh_query(sql, {"limit": limit})
    return [
        ServerResponse(
            server_id=r.get("server_id", ""),
            name=r.get("name", ""),
            risk_tier=r.get("risk_tier"),
            status=r.get("status", "active"),
            metadata=json.loads(r["metadata"]) if isinstance(r.get("metadata"), str) else r.get("metadata", {}),
        )
        for r in rows
    ]


def get_score_disputes_endpoint(session: AsyncSession) -> list[ScoreDisputeItem]:
    """Sync wrapper returning open disputes via service layer."""
    service = McpScoreDisputeService(session)
    disputes = asyncio.run(service.get_open_disputes())
    return [
        ScoreDisputeItem(
            dispute_id=d["dispute_id"],
            server_id=d["server_id"],
            axis=d["axis"],
            claimed_score=d["claimed_score"],
            status=d["status"],
            created_at=d.get("created_at"),
        )
        for d in disputes
    ]


# ── Self-test ────────────────────────────────────────────────────────────────

async def run_self_test() -> dict[str, str]:
    """Run self-test returning status dict."""
    from app.db import get_session as _get_session
    from app.models import McpServerRegistry as _McpSR, McpScoreDispute as _McpSD

    # Verify imports resolve
    assert _McpSR is not None
    assert _McpSD is not None

    # Verify all exported symbols present
    for sym in [
        "mesh_memory_endpoint",
        "mesh_memory_endpoint_get",
        "get_mesh_memory_endpoint",
        "signal_scores_endpoint",
        "critical_risk_servers_endpoint",
        "get_score_disputes_endpoint",
        "McpScoreDisputeService",
        "ServerResponse",
        "UserRead",
        "MeshMemoryItem",
        "SignalScoreItem",
        "ScoreDisputeItem",
    ]:
        assert sym in dir(), f"Missing export: {sym}"

    # Verify service class methods
    svc_methods = ["get_open_disputes", "create_dispute"]
    for m in svc_methods:
        assert hasattr(McpScoreDisputeService, m), f"Missing method: {m}"

    return {"status": "PASS", "tests": "all"}


async def test_self() -> dict[str, str]:
    """Alias for run_self_test."""
    return await run_self_test()


# ── Standalone self-test runner ───────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from app.db import get_session
    from app.main import app as main_app

    test_app = FastAPI(title="self-test")

    @test_app.get("/run_self_test")
    async def _run():
        return await run_self_test()

    # Override dependency for isolated test
    test_app.dependency_overrides[get_session] = lambda: None

    import httpx
    from httpx import ASGITransport

    async def _main():
        async with httpx.AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
            resp = await client.get("/run_self_test")
            result = resp.json()
            print(result.get("status", "FAIL"))
            assert result.get("status") == "PASS", f"Self-test failed: {result}"
            sys.exit(0)

    asyncio.run(_main())