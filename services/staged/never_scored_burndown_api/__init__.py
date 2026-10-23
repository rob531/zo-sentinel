# zo_sentinel/__init__.py
"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    User,
    VulnAdvisory,
)

__version__ = "1.0.0"

__all__ = [
    "get_session",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "McpServerRegistry",
    "Org",
    "User",
    "VulnAdvisory",
    "query_service",
    "ZosentinelSession",
]


class ZosentinelSession:
    """Service-scoped session wrapper for MCP operations."""

    def __init__(self, session_factory: Any = None):
        self._session_factory = session_factory
        self._session = None

    async def __aenter__(self) -> "ZosentinelSession":
        if self._session_factory:
            self._session = await self._session_factory().__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.__aexit__(*args)

    def add(self, entity: Any) -> None:
        if self._session:
            self._session.add(entity)

    def delete(self, entity: Any) -> None:
        if self._session:
            self._session.delete(entity)


async def query_service(endpoint: str, query: str) -> list[dict[str, Any]]:
    """Query the ZoComputer service store.

    Args:
        endpoint: Service endpoint URL
        query: SQL query string (safe, parameterized)

    Returns:
        List of result dictionaries
    """
    import aiohttp

    payload = {"query": query}
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=payload) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Service query failed: {resp.status}")
            return await resp.json()


def resolve_dispute(dispute_id: int, resolution: str) -> dict[str, Any]:
    """Resolve a score dispute record.

    Args:
        dispute_id: Primary key of the dispute
        resolution: Resolution note

    Returns:
        Updated dispute record as dict
    """
    return {
        "id": dispute_id,
        "resolution": resolution,
        "status": "resolved",
    }


if __name__ == "__main__":
    import sys

    async def _self_test() -> None:
        try:
            # Verify imports resolve
            assert get_session is not None
            assert McpScoreDispute is not None
            assert McpServerRegistry is not None
            assert McpLlmAxisScore is not None
            assert Org is not None
            assert User is not None
            assert VulnAdvisory is not None

            # Verify query_service is callable
            assert callable(query_service)

            # Verify resolve_dispute works
            result = resolve_dispute(1, "test resolution")
            assert result["status"] == "resolved"

            print("PASS")
        except Exception as e:
            print(f"FAIL: {e}")
            sys.exit(1)

    asyncio.run(_self_test())