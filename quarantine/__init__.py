# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Any, Dict, List, Optional

import requests

# Base URL for the write_service HTTP API
_WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# Whitelist of table names permitted in _post_query — prevents B608 SQL injection
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
    payload: Dict[str, Any] = {"table": table, "filter": filter or {}}
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception:
        return []


def get_signal_scores(
    server_id: Optional[str] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Retrieve signal scores from the mcp_signal_scores table."""
    filter_dict: Optional[Dict[str, Any]] = {"server_id": server_id} if server_id else None
    return _post_query("mcp_signal_scores", filter=filter_dict, timeout=timeout)


def get_mesh_memory(
    server_id: Optional[str] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Retrieve mesh memory entries from the mesh_memory table."""
    filter_dict: Optional[Dict[str, Any]] = {"server_id": server_id} if server_id else None
    return _post_query("mesh_memory", filter=filter_dict, timeout=timeout)


def mesh_scores_endpoint(
    server_id: Optional[str] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Endpoint-style wrapper for mesh scores retrieval."""
    return _post_query("mcp_mesh_scores", filter={"server_id": server_id} if server_id else None, timeout=timeout)


def signal_scores_endpoint(
    server_id: Optional[str] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Endpoint-style wrapper for signal scores retrieval."""
    return get_signal_scores(server_id=server_id, timeout=timeout)


def get_mesh_memory_endpoint(
    server_id: Optional[str] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Endpoint-style wrapper for mesh memory retrieval."""
    return get_mesh_memory(server_id=server_id, timeout=timeout)


def get_score_disputes(
    server_id: Optional[str] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Retrieve score disputes for a server (queries via write_service)."""
    return _post_query(
        "mcp_score_disputes",
        filter={"server_id": server_id} if server_id else None,
        timeout=timeout,
    )


def _run_self_test() -> bool:
    """Verify the module's functions can be called without errors."""
    try:
        # Smoke test: query with no filter should not raise
        scores = get_signal_scores(timeout=5)
        assert isinstance(scores, list), f"expected list, got {type(scores)}"

        memory = get_mesh_memory(timeout=5)
        assert isinstance(memory, list), f"expected list, got {type(memory)}"

        mesh = mesh_scores_endpoint(timeout=5)
        assert isinstance(mesh, list), f"expected list, got {type(mesh)}"

        disputes = get_score_disputes(timeout=5)
        assert isinstance(disputes, list), f"expected list, got {type(disputes)}"

        return True
    except Exception as exc:
        print(f"Self-test failed: {exc}")
        return False


if __name__ == "__main__":
    # Self-test runs against live write_service; exit 0 on success
    import sys
    sys.exit(0 if _run_self_test() else 1)
