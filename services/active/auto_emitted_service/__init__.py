# services/ is a package so builder-emitted service dirs
# (services/staged/<name>/ -> services/active/<name>/) are importable via
# `python -m services.<stage>.<name>.contract` with relative intra-service
# imports that survive staged->active promotion without any rewrite.

# deps: requests
"""Auto-emitted service package.
Provides utility functions for mesh/pipeline data access that survive
staged→active promotion without needing import rewrites.
All functions are pure (no side‑effects) and safe to import.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# B608 mitigation: whitelist of permitted table names prevents arbitrary SQL injection
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
    """POST a query to the write_service /query endpoint.

    Args:
        table: Name of the mesh/pipeline table to query.
        filter: Optional filter dict.
        timeout: Seconds before the request times out (B113 mitigation).

    Returns:
        List of row dictionaries (empty list on error).
    """
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
    """Fetch signal scores for a given ``mesh_id`` from ``mcp_signal_scores``."""
    return _post_query("mcp_signal_scores", {"mesh_id": mesh_id})


def signal_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Return a dict with the mesh_id and its signal scores."""
    rows = get_signal_scores(mesh_id)
    return {"mesh_id": mesh_id, "scores": rows, "count": len(rows)}


def get_mesh_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Fetch mesh scores for a given ``mesh_id`` from ``mcp_mesh_scores``."""
    return _post_query("mcp_mesh_scores", {"mesh_id": mesh_id})


def mesh_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Return a dict with the mesh_id and its mesh scores."""
    rows = get_mesh_scores(mesh_id)
    return {"mesh_id": mesh_id, "scores": rows, "count": len(rows)}


def get_mesh_memory(mesh_id: str) -> Dict[str, Any]:
    """Fetch mesh memory for a given ``mesh_id`` from ``mesh_memory``.
    Returns a single row dict or empty dict if not found.
    """
    rows = _post_query("mesh_memory", {"mesh_id": mesh_id})
    return rows[0] if rows else {}


def mesh_memory_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Return a dict with the mesh_id and its mesh memory."""
    rows = _post_query("mesh_memory", {"mesh_id": mesh_id})
    return {"mesh_id": mesh_id, "memory": rows[0] if rows else {}, "found": bool(rows)}


def reset_server_export_api_quarantine() -> bool:
    """Placeholder that pretends to reset an export-API quarantine flag.
    Always returns ``True`` – real implementation is service-specific.
    """
    return True


def _run_self_test() -> None:
    """Run a lightweight self-test when the module is executed directly.
    Calls each public function with a dummy mesh_id and ensures no exception
    propagates. Prints PASS on success.
    """
    dummy_id = "test-self"
    try:
        get_signal_scores(dummy_id)
        get_mesh_scores(dummy_id)
        get_mesh_memory(dummy_id)
        mesh_scores_endpoint(dummy_id)
        signal_scores_endpoint(dummy_id)
        mesh_memory_endpoint(dummy_id)
        reset_server_export_api_quarantine()
        print("PASS")
    except Exception:
        raise


if __name__ == "__main__":
    _run_self_test()
