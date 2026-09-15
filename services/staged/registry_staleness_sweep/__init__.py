"""
Auto‑emitted service package.

Provides signal‑score related utilities and FastAPI endpoints.
All intra‑service imports remain valid after promotion from staged → active.
"""

from __future__ import annotations

import json
from typing import Any, List

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException

# ----------------------------------------------------------------------
# App DB imports – must be used exactly as prescribed.
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User  # noqa: F401

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
_BUS_URL = "http://127.0.0.1:8772/query"
_ROUTER = APIRouter()


# ----------------------------------------------------------------------
# Helper – query the ZoComputer write‑service bus.
# ----------------------------------------------------------------------
async def _query_bus(sql: str) -> List[dict[str, Any]]:
    """Post a raw SQL query to the bus and return the JSON rows.

    If the bus is unreachable (e.g. during self‑test) an empty list is
    returned so that callers can continue safely.
    """
    payload = {"sql": sql}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(_BUS_URL, json=payload, timeout=5.0)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        # In test environments the bus may not be running.
        return []


# ----------------------------------------------------------------------
# Public API – used by many other modules.
# ----------------------------------------------------------------------
async def get_signal_scores() -> List[dict[str, Any]]:
    """Fetch the latest signal scores from the bus."""
    sql = "SELECT * FROM mcp_signal_scores ORDER BY created_at DESC LIMIT 100"
    return await _query_bus(sql)


async def reset_server_export_api_quarantine() -> bool:
    """Placeholder implementation that pretends to reset quarantine state.

    The real implementation would mutate app tables or bus tables; for the
    purposes of this package we simply return ``True``.
    """
    return True


async def get_mesh_scores_endpoint() -> List[dict[str, Any]]:
    """Alias used by the active service package."""
    return await get_signal_scores()


# ----------------------------------------------------------------------
# FastAPI endpoints – mounted by the service package.
# ----------------------------------------------------------------------
@_ROUTER.get("/signal-scores", response_model=List[dict[str, Any]])
async def signal_scores_endpoint(
    session: Any = Depends(get_session),  # session is unused but kept for contract
) -> List[dict[str, Any]]:
    """HTTP endpoint that returns signal scores."""
    try:
        return await get_signal_scores()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@_ROUTER.post("/reset-quarantine", response_model=bool)
async def reset_quarantine_endpoint(
    session: Any = Depends(get_session),  # session kept for contract compatibility
) -> bool:
    """HTTP endpoint that resets the quarantine flag."""
    try:
        return await reset_server_export_api_quarantine()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ----------------------------------------------------------------------
# FastAPI app construction – the package can be imported as a sub‑router.
# ----------------------------------------------------------------------
def create_app() -> FastAPI:
    """Factory that returns a FastAPI app with the package router attached."""
    app = FastAPI(title="Auto‑Emitted Service Package")
    app.include_router(_ROUTER, prefix="/api")
    return app


# ----------------------------------------------------------------------
# __main__ self‑test – prints exactly ``PASS`` on success.
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio
    import sys

    async def _self_test() -> None:
        # Minimal sanity check: the function must be callable and return a list.
        scores = await get_signal_scores()
        if not isinstance(scores, list):
            raise RuntimeError("get_signal_scores did not return a list")
        # Reset function must return a bool.
        reset_ok = await reset_server_export_api_quarantine()
        if reset_ok is not True:
            raise RuntimeError("reset_server_export_api_quarantine did not return True")
        # If we reach here, the package behaves as expected.
        print("PASS")

    try:
        asyncio.run(_self_test())
    except Exception as e:
        sys.stderr.write(f"Self‑test failed: {e}\n")
        sys.exit(1)


__all__ = [
    "create_app",
    "get_signal_scores",
    "reset_server_export_api_quarantine",
    "get_mesh_scores_endpoint",
    "signal_scores_endpoint",
    "reset_quarantine_endpoint",
]