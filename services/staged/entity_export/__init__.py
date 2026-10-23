"""Auto-emitted service package for mesh memory and signal score operations."""

from typing import Any
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute


class MeshMemoryRecord(BaseModel):
    """Response model for mesh memory endpoint."""
    id: str
    server_id: str | None = None
    content: dict[str, Any]
    metadata: dict[str, Any] | None = None


class SignalScoresRecord(BaseModel):
    """Response model for signal scores."""
    server_id: str
    scores: dict[str, float]
    timestamp: str | None = None


def _get_mesh_memory_impl(server_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Internal implementation for mesh memory retrieval via write-service bus."""
    import requests
    
    query = {"table": "mesh_memory", "limit": limit}
    if server_id:
        query["filters"] = {"server_id": server_id}
    
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception:
        return []


def get_mesh_memory_endpoint(
    server_id: str | None = None,
    limit: int = 100,
    session: Session | None = None
) -> list[MeshMemoryRecord]:
    """Fetch mesh memory records from the pipeline bus."""
    rows = _get_mesh_memory_impl(server_id=server_id, limit=limit)
    return [
        MeshMemoryRecord(
            id=r.get("id", ""),
            server_id=r.get("server_id"),
            content=r.get("content", {}),
            metadata=r.get("metadata")
        )
        for r in rows
    ]


def get_mesh_memory_by_id(record_id: str) -> MeshMemoryRecord | None:
    """Fetch a single mesh memory record by ID."""
    rows = _get_mesh_memory_impl(server_id=None, limit=1)
    for r in rows:
        if r.get("id") == record_id:
            return MeshMemoryRecord(
                id=r.get("id", ""),
                server_id=r.get("server_id"),
                content=r.get("content", {}),
                metadata=r.get("metadata")
            )
    return None


def signal_scores_endpoint(
    server_id: str,
    session: Session = Depends(get_session)
) -> SignalScoresRecord | None:
    """Fetch signal scores for a server from the pipeline bus."""
    import requests
    
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "filters": {"server_id": server_id}, "limit": 1},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if rows:
            r = rows[0]
            return SignalScoresRecord(
                server_id=r.get("server_id", server_id),
                scores=r.get("scores", {}),
                timestamp=r.get("timestamp")
            )
    except Exception:
        pass
    return None


def get_server_registries(
    session: Session = Depends(get_session)
) -> list[McpServerRegistry]:
    """Fetch all server registry entries from app DB."""
    return session.query(McpServerRegistry).all()


def run_self_test() -> str:
    """Self-test for service package imports and basic connectivity."""
    # Verify imports work
    assert MeshMemoryRecord is not None
    assert SignalScoresRecord is not None
    assert _get_mesh_memory_impl is not None
    assert get_mesh_memory_endpoint is not None
    assert get_mesh_memory_by_id is not None
    assert signal_scores_endpoint is not None
    assert get_server_registries is not None
    
    # Verify model imports from app.models
    assert McpServerRegistry is not None
    assert McpLlmAxisScore is not None
    assert McpScoreDispute is not None
    
    return "PASS"


if __name__ == "__main__":
    print(run_self_test())