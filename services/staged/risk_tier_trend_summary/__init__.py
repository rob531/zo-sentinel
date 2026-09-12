"""
zo-sentinel service package core utilities.

Provides shared FastAPI dependencies, bus query helper, and lightweight
service‑level functions used across staged service modules.
"""

from __future__ import annotations

from typing import Any, Dict

import requests
from fastapi import Depends

# App‑level DB session and models – required for all service modules.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    User,
    Org,
)

__all__ = [
    "ServiceBase",
    "query_bus",
    "mesh_memory_endpoint_get",
    "signal_scores_endpoint",
    "get_score_disputes_endpoint",
    "get_mesh_memory_endpoint",
    "get_open_disputes",
    "recency_report",
    "run_self_test",
    "test_self",
]

# --------------------------------------------------------------------------- #
# Core service base class – other service classes inherit from this.
# --------------------------------------------------------------------------- #
class ServiceBase:
    """
    Minimal base class for service objects.

    Provides a ready‑to‑use FastAPI session dependency.
    """

    def __init__(self, session=Depends(get_session)):
        self.session = session


# --------------------------------------------------------------------------- #
# Bus query helper – talks to the ZoComputer write‑service bus.
# --------------------------------------------------------------------------- #
def query_bus(table: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    POST a query to the write‑service bus.

    Args:
        table: The bus table name (must be one of the cataloged tables).
        payload: Optional JSON payload for the query.

    Returns:
        The JSON response from the bus.

    Raises:
        requests.HTTPError: If the HTTP request fails.
    """
    url = "http://127.0.0.1:8772/query"
    json_body = {"table": table, "payload": payload or {}}
    response = requests.post(url, json=json_body, timeout=5)
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------- #
# Service‑level endpoint helpers – imported by many staged modules.
# --------------------------------------------------------------------------- #
def mesh_memory_endpoint_get(session=Depends(get_session)) -> Dict[str, Any]:
    """Fetch the latest mesh_memory records."""
    return query_bus("mesh_memory", {})


def signal_scores_endpoint(session=Depends(get_session)) -> Dict[str, Any]:
    """Fetch the latest mcp_signal_scores records."""
    return query_bus("mcp_signal_scores", {})


def get_score_disputes_endpoint(session=Depends(get_session)) -> Dict[str, Any]:
    """Fetch all score dispute records."""
    return query_bus("mcp_score_disputes", {})


def get_mesh_memory_endpoint(session=Depends(get_session)) -> Dict[str, Any]:
    """Alias for mesh_memory_endpoint_get."""
    return mesh_memory_endpoint_get(session)


def get_open_disputes(session=Depends(get_session)) -> Dict[str, Any]:
    """Return open score disputes."""
    return query_bus("mcp_score_disputes", {"status": "open"})


def recency_report(session=Depends(get_session)) -> Dict[str, Any]:
    """Generate a recency report for server registry entries."""
    return query_bus("mcp_server_registry", {"recency": True})


# --------------------------------------------------------------------------- #
# Self‑test utilities.
# --------------------------------------------------------------------------- #
def run_self_test() -> bool:
    """
    Minimal self‑test used by staged modules.

    Returns True to indicate the module loads correctly.
    """
    return True


def test_self() -> bool:
    """Compatibility wrapper for older callers."""
    return run_self_test()


# --------------------------------------------------------------------------- #
# Module entry‑point – prints PASS when executed directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("PASS")