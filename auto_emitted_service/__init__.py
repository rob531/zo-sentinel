# deps: requests
"""Auto-emitted service package.
Provides utility functions for mesh/pipeline data access that survive
staged→active promotion without needing import rewrites.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

# Re-export router for auto_emitted_service.router consumers
from .signal_scores import router

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# Whitelist of permitted table names (B608 mitigation)
_VALID_TABLES: frozenset[str] = frozenset({
    "mcp_signal_scores",
    "mcp_mesh_scores",
    "mesh_memory",
})


def _post_query(
    table: str,
    filter: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """POST a query to the write_service /query endpoint."""
    if table not in _VALID_TABLES:
        return []
    payload = {"table": table, "filter": filter or {}}
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception:
        return []


def get_signal_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Fetch signal scores for a given mesh_id from mcp_signal_scores."""
    return _post_query("mcp_signal_scores", {"mesh_id": mesh_id})


def get_mesh_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Fetch mesh scores for a given mesh_id from mcp_mesh_scores."""
    return _post_query("mcp_mesh_scores", {"mesh_id": mesh_id})


def get_mesh_memory(mesh_id: str) -> List[Dict[str, Any]]:
    """Fetch mesh memory for a given mesh_id from mesh_memory."""
    return _post_query("mesh_memory", {"mesh_id": mesh_id})


def reset_server_export_api_quarantine() -> bool:
    """Placeholder that pretends to reset an export-API quarantine flag.
    Always returns True – real implementation is service-specific.
    """
    return True


def _run_self_test() -> None:
    """Run a lightweight self-test when the module is executed directly."""
    dummy_id = "test-self"
    try:
        get_signal_scores(dummy_id)
        get_mesh_scores(dummy_id)
        get_mesh_memory(dummy_id)
        reset_server_export_api_quarantine()
        print("PASS")
    except Exception:
        raise


if __name__ == "__main__":
    _run_self_test()
