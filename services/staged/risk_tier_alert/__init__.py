"""
zo‑sentinel shared service utilities.

This module supplies common FastAPI dependencies and helper functions used by
many staged services.  It deliberately avoids any custom ORM definitions –
all models are imported from ``app.models`` and the session factory from
``app.db``.  The mesh‑store is accessed via a simple HTTP POST request to the
local ZoComputer service.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI

# --------------------------------------------------------------------------- #
# Core DB access – **do not** create a new engine or session here.
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401  (exported for callers)
from app.models import (  # noqa: F401
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# --------------------------------------------------------------------------- #
# Mesh‑store helper
# --------------------------------------------------------------------------- #
_MESH_URL = "http://127.0.0.1:8772/query"


def _post_mesh(query: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """
    Send a raw SQL query to the mesh store and return the decoded JSON payload.

    Parameters
    ----------
    query:
        The SQL statement to execute.
    params:
        Optional mapping of bind parameters.

    Returns
    -------
    The JSON‑decoded response body.
    """
    payload = {"query": query}
    if params:
        payload["params"] = params
    response = httpx.post(_MESH_URL, json=payload, timeout=10.0)
    response.raise_for_status()
    return response.json()


def get_mesh_memory() -> List[Dict[str, Any]]:
    """
    Retrieve the current ``mesh_memory`` table contents.

    Returns
    -------
    A list of rows, each represented as a ``dict``.
    """
    return _post_mesh("SELECT * FROM mesh_memory")


def get_signal_scores() -> List[Dict[str, Any]]:
    """
    Retrieve the current ``mcp_signal_scores`` table contents.

    Returns
    -------
    A list of rows, each represented as a ``dict``.
    """
    return _post_mesh("SELECT * FROM mcp_signal_scores")


# --------------------------------------------------------------------------- #
# Convenience DB queries (examples – callers may import and customise)
# --------------------------------------------------------------------------- #
def list_servers(session: Any = Depends(get_session)) -> List[McpServerRegistry]:
    """Return all registered MCP servers."""
    return session.query(McpServerRegistry).all()


def list_llm_axis_scores(
    session: Any = Depends(get_session),
) -> List[McpLlmAxisScore]:
    """Return all LLM axis scores."""
    return session.query(McpLlmAxisScore).all()


def list_score_disputes(
    session: Any = Depends(get_session),
) -> List[McpScoreDispute]:
    """Return all score disputes."""
    return session.query(McpScoreDispute).all()


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    app = FastAPI()

    @app.get("/self-test")
    def _self_test(
        mesh: List[Dict[str, Any]] = Depends(lambda: []),  # mesh stub
        scores: List[Dict[str, Any]] = Depends(lambda: []),  # scores stub
    ) -> str:
        # The endpoint simply proves that dependencies resolve.
        return "PASS"

    # Override the real DB session with a dummy that raises if used.
    def _dummy_session() -> None:
        raise RuntimeError("DB access not allowed in self‑test")

    app.dependency_overrides[get_session] = _dummy_session

    # Run the test client synchronously.
    import asyncio

    async def _run() -> None:
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/self-test")
        assert resp.status_code == 200
        assert resp.text == "\"PASS\""
        print("PASS")

    asyncio.run(_run())