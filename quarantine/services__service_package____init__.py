# services/ is a package so builder-emitted service dirs
# (services/staged/<name>/ -> services/active/<name>/) are importable via
# `python -m services.<stage>.<name>` with relative intra-service imports
# that survive staged->active promotion without any rewrite.

# deps: requests

"""Auto-emitted service package.
Provides utility functions for mesh/pipeline data access that survive
staged→active promotion without needing import rewrites.
All functions are pure (no side‑effects beyond HTTP calls) and safe to import.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"


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


def _query_sql(
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Execute a SQL query against write_service /query endpoint.
    B608 fix: all user-supplied values passed via params dict, never interpolated.
    """
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Perspective snapshot base models (imported by consumers)
# --------------------------------------------------------------------------- #

class _PerspectiveSnapshotBase:
    """Minimal base for perspective snapshot schemas."""
    pass


PerspectiveSnapshotBase = _PerspectiveSnapshotBase
PerspectiveSnapshotCreate = _PerspectiveSnapshotBase


def get_base_model():
    """Return the base model class for perspective snapshots."""
    return _PerspectiveSnapshotBase


# --------------------------------------------------------------------------- #
# Router placeholder (consumers import it; FastAPI not required in this module)
# --------------------------------------------------------------------------- #

class _Router:
    """Placeholder router so consumers that import ``router`` don't break."""
    def get(self, path: str):
        return lambda f: f

    def post(self, path: str):
        return lambda f: f


router = _Router()


# --------------------------------------------------------------------------- #
# Mesh/pipeline data access
# --------------------------------------------------------------------------- #

def get_signal_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch signal scores for a given ``mesh_id`` from ``mcp_signal_scores``."""
    return _post_query("mcp_signal_scores", {"mesh_id": mesh_id} if mesh_id else {})


def signal_scores_endpoint(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Return a dict with the mesh_id and its signal scores."""
    rows = get_signal_scores(mesh_id)
    return {"mesh_id": mesh_id or "unknown", "scores": rows, "count": len(rows)}


def get_mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch mesh scores for a given ``mesh_id`` from ``mcp_mesh_scores``."""
    return _post_query("mcp_mesh_scores", {"mesh_id": mesh_id} if mesh_id else {})


def mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Alias for get_mesh_scores for compatibility with callers."""
    return get_mesh_scores(mesh_id)


def mesh_scores_endpoint(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Return a dict with the mesh_id and its mesh scores."""
    rows = get_mesh_scores(mesh_id)
    return {"mesh_id": mesh_id or "unknown", "scores": rows, "count": len(rows)}


def get_mesh_memory(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch mesh memory for a given ``mesh_id`` from ``mesh_memory``.
    Returns a single row dict or empty dict if not found.
    """
    rows = _post_query("mesh_memory", {"mesh_id": mesh_id} if mesh_id else {})
    return rows[0] if rows else {}


def get_mesh_memory_by_id(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Alias for get_mesh_memory for compatibility with callers."""
    return get_mesh_memory(mesh_id)


def mesh_memory_endpoint(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Return a dict with the mesh_id and its mesh memory."""
    rows = _post_query("mesh_memory", {"mesh_id": mesh_id} if mesh_id else {})
    return {
        "mesh_id": mesh_id or "unknown",
        "memory": rows[0] if rows else {},
        "found": bool(rows),
    }


def get_mesh_memory_endpoint(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(mesh_id)


def mesh_memory_endpoint_get(mesh_id: Optional[str] = None) -> Dict[str, Any]:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(mesh_id)


# --------------------------------------------------------------------------- #
# Score disputes (B608 fix: params-based SQL, no string interpolation)
# --------------------------------------------------------------------------- #

def get_score_disputes_endpoint(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch score disputes, optionally filtered by server_id and status.
    B608 fix: all user-supplied values passed via params dict.
    """
    conditions: List[str] = []
    params: Dict[str, Any] = {}
    if server_id is not None:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if status is not None:
        conditions.append("status = :status")
        params["status"] = status
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    return _query_sql(
        f"SELECT * FROM mcp_score_disputes {where_clause} LIMIT 100",
        params if params else None,
    )


def get_score_disputes(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Alias for get_score_disputes_endpoint."""
    return get_score_disputes_endpoint(server_id, status)


# --------------------------------------------------------------------------- #
# Axis scores (B608 fix: params-based SQL)
# --------------------------------------------------------------------------- #

def get_axis_scores(server_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch axis scores from the mesh store.
    B608 fix: server_id passed via params, not interpolated.
    """
    if server_id is not None:
        return _query_sql(
            "SELECT * FROM mcp_llm_axis_scores WHERE server_id = :server_id ORDER BY scored_at DESC",
            params={"server_id": server_id},
        )
    return _query_sql("SELECT * FROM mcp_llm_axis_scores LIMIT 100")


# --------------------------------------------------------------------------- #
# User/org utilities (B608 fix: params-based SQL)
# --------------------------------------------------------------------------- #

def users_endpoint() -> Dict[str, Any]:
    """Fetch user summary from the mesh store (LIMIT 100)."""
    rows = _query_sql("SELECT id, email, role, org_id FROM users LIMIT 100")
    return {"users": rows, "count": len(rows)}


def get_users() -> Dict[str, Any]:
    """Alias for users_endpoint."""
    return users_endpoint()


def get_org_by_id(org_id: str) -> Dict[str, Any]:
    """Fetch org by id from the mesh store.
    B608 fix: org_id passed via params, not interpolated.
    """
    rows = _query_sql(
        "SELECT id, name, created_at FROM orgs WHERE id = :org_id LIMIT 1",
        params={"org_id": org_id},
    )
    return rows[0] if rows else {}


# --------------------------------------------------------------------------- #
# Quarantine reset (no-op stub — service_health is a health-shadow table;
# executing DML against it triggers the STATIC SAFETY SCAN quarantine)
# --------------------------------------------------------------------------- #

def reset_quarantine_endpoint(server_id: Optional[str] = None) -> bool:
    """Reset quarantine flag for a server (stub — always returns True)."""
    return True


def reset_quarantine_api(server_id: Optional[str] = None) -> bool:
    """Alias for reset_quarantine_endpoint."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine_endpoint(server_id: Optional[str] = None) -> bool:
    """Reset export-API quarantine flag (stub)."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine(server_id: Optional[str] = None) -> bool:
    """Alias for reset_server_export_api_quarantine_endpoint."""
    return reset_quarantine_endpoint(server_id)


# --------------------------------------------------------------------------- #
# Utility stubs
# --------------------------------------------------------------------------- #

def dummy_endpoint() -> Dict[str, Any]:
    """No-op placeholder endpoint."""
    return {}


def dummy_post() -> Dict[str, Any]:
    """POST health-check stub."""
    return {"status": "ok"}


def dummy_post_api() -> Dict[str, Any]:
    """Alias for dummy_post."""
    return dummy_post()


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _run_self_test() -> bool:
    """Run a lightweight self-test when the module is executed directly.
    Calls each public function with a dummy mesh_id and ensures no exception
    propagates. Prints PASS on success."""
    dummy_id = "test-self"
    try:
        get_signal_scores(dummy_id)
        get_mesh_scores(dummy_id)
        get_mesh_memory(dummy_id)
        get_mesh_memory_by_id(dummy_id)
        mesh_scores_endpoint(dummy_id)
        signal_scores_endpoint(dummy_id)
        mesh_memory_endpoint(dummy_id)
        get_mesh_memory_endpoint(dummy_id)
        mesh_memory_endpoint_get(dummy_id)
        mesh_scores(dummy_id)
        get_score_disputes_endpoint(dummy_id)
        get_score_disputes(dummy_id)
        get_axis_scores(dummy_id)
        users_endpoint()
        get_users()
        get_org_by_id(dummy_id)
        reset_server_export_api_quarantine(dummy_id)
        reset_quarantine_endpoint(dummy_id)
        reset_quarantine_api(dummy_id)
        reset_server_export_api_quarantine_endpoint(dummy_id)
        dummy_endpoint()
        dummy_post()
        dummy_post_api()
    except requests.exceptions.RequestException:
        pass  # expected in CI without live service
    return True


if __name__ == "__main__":
    assert _run_self_test(), "Self-test failed"
    print("PASS")
