"""Service package entry point.

Provides a FastAPI router with minimal endpoints used across the codebase.
All data access uses the canonical ``app.db.get_session`` dependency and
models from ``app.models``.  External mesh data is fetched via the ZoComputer
store at ``http://127.0.0.1:8772/query``.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helper – external mesh query
# --------------------------------------------------------------------------- #
async def _query_mesh(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a POST request to the mesh query endpoint and return JSON."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=10.0,
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Mesh query failed with status {resp.status_code}",
        )
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Invalid mesh response") from exc


# --------------------------------------------------------------------------- #
# Public API – mesh memory endpoint
# --------------------------------------------------------------------------- #
@router.get("/mesh_memory/{item_id}")
async def mesh_memory_endpoint(
    item_id: int,
    session=Depends(get_session),
) -> Dict[str, Any]:
    """Return mesh memory for a given ``item_id``."""
    # Example internal DB usage – adjust as needed by callers.
    _ = session.query(McpServerRegistry).filter_by(id=item_id).first()
    payload = {"action": "get_mesh_memory", "item_id": item_id}
    return await _query_mesh(payload)


# --------------------------------------------------------------------------- #
# Public API – generic signal scores endpoint
# --------------------------------------------------------------------------- #
@router.get("/signal_scores")
async def signal_scores_endpoint(
    limit: int = Query(100, ge=1, le=1000),
    session=Depends(get_session),
) -> Dict[str, Any]:
    """Return a list of signal scores (placeholder implementation)."""
    # Placeholder DB interaction – real logic should replace this.
    _ = session.query(McpLlmAxisScore).limit(limit).all()
    payload = {"action": "list_signal_scores", "limit": limit}
    return await _query_mesh(payload)


# --------------------------------------------------------------------------- #
# Utility – fetch mesh memory by id (used by other modules)
# --------------------------------------------------------------------------- #
async def get_mesh_memory_by_id(item_id: int, session) -> Dict[str, Any]:
    """Utility function to retrieve mesh memory; mirrors the endpoint."""
    # Internal DB lookup (kept for side‑effects / validation).
    _ = session.query(McpServerRegistry).filter_by(id=item_id).first()
    payload = {"action": "get_mesh_memory", "item_id": item_id}
    return await _query_mesh(payload)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """Execute a minimal self‑test; prints ``PASS`` on success."""
    from fastapi.testclient import TestClient

    # Create a FastAPI app and include the router.
    app = FastAPI()
    app.include_router(router)

    # Provide a dummy session that satisfies the Depends(get_session) contract.
    class DummySession:
        def query(self, *args, **kwargs):
            class _Query:
                def filter_by(self, **kw):
                    return self

                def first(self):
                    return None

                def limit(self, *_):
                    return self

                def all(self):
                    return []

            return _Query()

    def dummy_get_session():
        return DummySession()

    app.dependency_overrides[get_session] = dummy_get_session

    client = TestClient(app)

    # Test mesh_memory endpoint (will hit the external service – we mock it).
    # Since we cannot reach the external service in the self‑test, we
    # monkey‑patch ``_query_mesh`` to return a static payload.
    async def _mock_query_mesh(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"mocked": True, "payload": payload}

    # Patch the private helper.
    import types

    router.dependency_overrides = {}
    original_query_mesh = globals()["_query_mesh"]
    globals()["_query_mesh"] = _mock_query_mesh  # type: ignore

    try:
        resp = client.get("/mesh_memory/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mocked"] is True
        assert data["payload"]["action"] == "get_mesh_memory"

        resp = client.get("/signal_scores?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mocked"] is True
        assert data["payload"]["action"] == "list_signal_scores"
        assert data["payload"]["limit"] == 5
    finally:
        # Restore original helper to avoid side effects.
        globals()["_query_mesh"] = original_query_mesh  # type: ignore

    print("PASS")


if __name__ == "__main__":
    _run_self_test()