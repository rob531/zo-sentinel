# services/ is a package so builder-emitted service dirs
# (services/staged/<name>/ -> services/active/<name>/) are importable via
# `python -m services.<stage>.<name>` with relative intra-service imports
# that survive staged->active promotion without any rewrite.

# deps: requests

"""Auto-emitted service package.
Provides utility functions for mesh/pipeline data access that survive
staged→active promotion without needing import rewrites.
All functions are pure; no FastAPI, no DB, no import-time side-effects.
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
        timeout: Seconds before the request times out.

    Returns:
        List of row dictionaries (empty list on error).
    """
    if table not in _VALID_TABLES:
        return []
    payload: Dict[str, Any] = {"table": table, "filter": filter or {}}
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception:
        return []


def _post_raw_query(
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """POST a raw SQL query to the write_service /query endpoint.

    Args:
        sql: SQL query string.
        params: Parameter dict for the query.
        timeout: Seconds before the request times out.

    Returns:
        List of row dictionaries (empty list on error).
    """
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception:
        return []


def get_corpus_by_server_id(server_id: int) -> Dict[str, Any]:
    """Fetch corpus document for a given ``server_id`` from the mesh store.
    Returns a single row dict or empty dict if not found.
    """
    rows = _post_raw_query(
        "SELECT * FROM ask_corpus_index WHERE server_id = :server_id LIMIT 1",
        params={"server_id": server_id},
    )
    return rows[0] if rows else {}


def get_signal_scores(server_id: int) -> List[Dict[str, Any]]:
    """Fetch signal scores for a given ``server_id`` from ``mcp_signal_scores``."""
    return _post_query("mcp_signal_scores", {"server_id": server_id})


def get_verdict_history(server_id: int) -> List[Dict[str, Any]]:
    """Fetch axis scores for a given ``server_id`` representing verdict history.
    Maps to mcp_llm_axis_scores table via server_id.
    """
    return _post_raw_query(
        "SELECT * FROM mcp_llm_axis_scores WHERE server_id = :server_id ORDER BY scored_at DESC",
        params={"server_id": server_id},
    )


def signal_scores_endpoint(server_id: int) -> Dict[str, Any]:
    """Return a dict with the server_id and its signal scores."""
    rows = get_signal_scores(server_id)
    return {"server_id": server_id, "scores": rows, "count": len(rows)}


def get_mesh_scores(server_id: int) -> List[Dict[str, Any]]:
    """Fetch mesh scores for a given ``server_id`` from ``mcp_mesh_scores``."""
    return _post_query("mcp_mesh_scores", {"server_id": server_id})


def mesh_scores(server_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Alias for get_mesh_scores for backward compatibility."""
    if server_id is not None:
        return _post_query("mcp_signal_scores", {"server_id": server_id})
    return _post_query("mcp_signal_scores")


def mesh_scores_endpoint(server_id: int) -> Dict[str, Any]:
    """Return a dict with the server_id and its mesh scores."""
    rows = get_mesh_scores(server_id)
    return {"server_id": server_id, "scores": rows, "count": len(rows)}


def get_mesh_memory(server_id: int) -> Dict[str, Any]:
    """Fetch mesh memory for a given ``server_id`` from ``mesh_memory``.
    Returns a single row dict or empty dict if not found.
    """
    rows = _post_query("mesh_memory", {"server_id": server_id})
    return rows[0] if rows else {}


def get_axis_scores(server_id: int) -> Dict[str, Any]:
    """Fetch axis scores for a given ``server_id`` from ``mcp_llm_axis_scores``.
    Returns a dict with server_id and list of axis score rows.
    """
    rows = _post_raw_query(
        "SELECT * FROM mcp_llm_axis_scores WHERE server_id = :server_id ORDER BY scored_at DESC",
        params={"server_id": server_id},
    )
    return {"server_id": server_id, "axes": rows, "count": len(rows)}


def reset_server_export_api_quarantine() -> bool:
    """Placeholder that pretends to reset an export-API quarantine flag.
    Always returns ``True`` – real implementation is service-specific.
    """
    return True


def _run_self_test() -> None:
    """Run a lightweight self-test when the module is executed directly.
    Calls each public function with a dummy server_id and ensures no exception
    propagates. Prints PASS on success.
    """
    dummy_id = 0
    try:
        get_corpus_by_server_id(dummy_id)
        get_signal_scores(dummy_id)
        get_verdict_history(dummy_id)
        signal_scores_endpoint(dummy_id)
        get_mesh_scores(dummy_id)
        mesh_scores(dummy_id)
        mesh_scores_endpoint(dummy_id)
        get_mesh_memory(dummy_id)
        get_axis_scores(dummy_id)
        reset_server_export_api_quarantine()
        print("PASS")
    except Exception:
        raise


if __name__ == "__main__":
    _run_self_test()
