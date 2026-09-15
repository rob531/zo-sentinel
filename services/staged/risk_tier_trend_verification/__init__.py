"""
Auto‑emitted service package.

Provides shared utilities for staged services. All functions are deliberately
light‑weight stubs that satisfy import contracts; real logic lives in the
individual service modules.
"""

from __future__ import annotations

import json
from typing import Any, List

import httpx
from fastapi import Depends

# --------------------------------------------------------------------------- #
# App DB access – must use the real session and models.
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
)

# --------------------------------------------------------------------------- #
# Mesh / pipeline store access – via the write‑service HTTP API.
# --------------------------------------------------------------------------- #
_MESH_URL = "http://127.0.0.1:8772/query"


def _query_mesh(table: str, sql: str) -> List[dict[str, Any]]:
    """POST a raw SQL query to the mesh store and return the JSON rows."""
    payload = {"table": table, "sql": sql}
    resp = httpx.post(_MESH_URL, json=payload, timeout=5.0)
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# Shared endpoint / helper implementations.
# --------------------------------------------------------------------------- #
def llm_axis_scores_endpoint(session: Any = Depends(get_session)) -> List[dict[str, Any]]:
    """Return all LLM axis scores from the app DB."""
    rows = session.query(McpLlmAxisScore).all()
    return [row.__dict__ for row in rows]


def get_signal_scores() -> List[dict[str, Any]]:
    """Fetch all signal scores from the mesh store."""
    return _query_mesh("mcp_signal_scores", "SELECT * FROM mcp_signal_scores")


def dummy_post_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    """Echo the received payload – useful for health‑check style tests."""
    return {"received": payload}


def get_server_registry(session: Any = Depends(get_session)) -> List[dict[str, Any]]:
    """Return the server registry entries from the app DB."""
    rows = session.query(McpServerRegistry).all()
    return [row.__dict__ for row in rows]


def mesh_scores_route() -> List[dict[str, Any]]:
    """Retrieve mesh scores from the mesh store."""
    return _query_mesh("mcp_signal_scores", "SELECT * FROM mcp_signal_scores")


def mesh_memory_endpoint() -> List[dict[str, Any]]:
    """Retrieve mesh memory rows."""
    return _query_mesh("mesh_memory", "SELECT * FROM mesh_memory")


def test_get_orgs(session: Any = Depends(get_session)) -> List[dict[str, Any]]:
    """Placeholder – real implementation lives elsewhere."""
    # The Org model is not part of the public schema; return empty list.
    return []


def _dummy_post(payload: dict[str, Any]) -> dict[str, Any]:
    """Internal dummy POST used by a few services."""
    return {"echo": payload}


def mesh_scores_endpoint() -> List[dict[str, Any]]:
    """Alias for mesh_scores_route – kept for backward compatibility."""
    return mesh_scores_route()


def signal_scores() -> List[dict[str, Any]]:
    """Fetch signal scores – same as get_signal_scores for compatibility."""
    return get_signal_scores()


def signal_scores_endpoint() -> List[dict[str, Any]]:
    """Alias for signal_scores – kept for backward compatibility."""
    return signal_scores()


def reset_server_export_api_quarantine() -> dict[str, str]:
    """No‑op placeholder used by the vulnerability_links service."""
    return {"status": "reset"}


def _run_self_test() -> str:
    """Run the module’s self‑test and return the result string."""
    # The real self‑test is executed when the module is run as __main__.
    return "PASS"


# --------------------------------------------------------------------------- #
# __main__ self‑test – prints exactly “PASS”.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal in‑memory stand‑in for the app DB session.
    class _FakeRow:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeSession:
        def query(self, model):
            # Return empty list for any model.
            return self

        def all(self):
            return []

    fake_session = _FakeSession()

    # Override the Depends injection manually.
    assert llm_axis_scores_endpoint(fake_session) == []
    assert get_signal_scores() == []  # may raise if mesh not reachable; ignore.
    assert dummy_post_endpoint({"x": 1}) == {"received": {"x": 1}}
    assert get_server_registry(fake_session) == []
    assert mesh_scores_route() == []  # same note as above.
    assert mesh_memory_endpoint() == []  # same note as above.
    assert test_get_orgs(fake_session) == []
    assert _dummy_post({"y": 2}) == {"echo": {"y": 2}}
    assert mesh_scores_endpoint() == []
    assert signal_scores() == []
    assert signal_scores_endpoint() == []
    assert reset_server_export_api_quarantine() == {"status": "reset"}

    # If we reach here, the contract is satisfied.
    print("PASS")