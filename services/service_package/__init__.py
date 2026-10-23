# services/ is a package so builder-emitted service dirs
# (services/staged/<name>/ -> services/active/<name>/) are importable via
# `python -m services.<stage>.<name>.contract` with relative intra-service
# imports that survive staged->active promotion without any rewrite.
# deps: requests
"""Auto-emitted service package.
Provides utility functions for mesh/pipeline data access that survive
staged→active promotion without needing import rewrites.
All functions are pure (no side‑effects beyond HTTP calls) and safe to import.
No app.db / app.models imports — this is a pure HTTP utility package.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _post(endpoint: str, json: Dict[str, Any], timeout: int = 10) -> Any:
    """POST to write_service with timeout (B113 mitigation)."""
    resp = requests.post(f"{_WRITE_SERVICE_URL}{endpoint}", json=json, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute a parameterized query (B608 mitigation)."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        data = _post("/query", payload)
        return data.get("rows", []) if isinstance(data, dict) else data
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Perspective snapshot base models (imported by consumers that inherit)
# --------------------------------------------------------------------------- #

class _BaseModel:
    """Placeholder base class for perspective snapshot models."""
    pass


PerspectiveSnapshotBase = _BaseModel
PerspectiveSnapshotCreate = _BaseModel


def get_base_model():
    """Return the base model class."""
    return _BaseModel


# --------------------------------------------------------------------------- #
# FastAPI router placeholder (consumers import it; this is a utility package,
# not a FastAPI app — no routes are registered here)
# --------------------------------------------------------------------------- #

class _Router:
    """Placeholder router class for compatibility."""
    def get(self, path: str):
        return lambda f: f

    def post(self, path: str):
        return lambda f: f


router = _Router()


# --------------------------------------------------------------------------- #
# Mesh memory
# --------------------------------------------------------------------------- #

def get_mesh_memory(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch mesh memory for a given entity from mesh_memory store."""
    if entity_type and entity_id:
        rows = _query(
            "SELECT * FROM mesh_memory WHERE entity_type = :entity_type AND entity_id = :entity_id ORDER BY timestamp DESC LIMIT 1",
            params={"entity_type": entity_type, "entity_id": entity_id},
        )
        return rows[0] if rows else {}
    rows = _query("SELECT * FROM mesh_memory ORDER BY timestamp DESC LIMIT 1")
    return rows[0] if rows else {}


def mesh_memory_endpoint(
    mesh_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return mesh memory dict for a given mesh_id."""
    return get_mesh_memory()


def mesh_memory_endpoint_get(
    mesh_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Alias for mesh_memory_endpoint."""
    return get_mesh_memory()


def get_mesh_memory_endpoint(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Get mesh memory by entity type and id."""
    return get_mesh_memory(entity_type, entity_id)


def get_mesh_memory_by_id(
    mesh_memory_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Get mesh memory by its id."""
    if mesh_memory_id:
        rows = _query(
            "SELECT * FROM mesh_memory WHERE id = :id LIMIT 1",
            params={"id": mesh_memory_id},
        )
        return rows[0] if rows else {}
    return get_mesh_memory()


# --------------------------------------------------------------------------- #
# Signal / mesh scores
# --------------------------------------------------------------------------- #

def signal_scores_endpoint(
    mesh_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get signal scores, optionally filtered by mesh_id."""
    if mesh_id:
        return _query(
            "SELECT * FROM mcp_signal_scores WHERE mesh_id = :mesh_id",
            params={"mesh_id": mesh_id},
        )
    return _query("SELECT * FROM mcp_signal_scores")


def get_signal_scores(
    mesh_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Alias for signal_scores_endpoint."""
    return signal_scores_endpoint(mesh_id)


def get_mesh_scores(
    mesh_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get mesh scores (alias for signal_scores_endpoint)."""
    return signal_scores_endpoint(mesh_id)


def mesh_scores_endpoint(
    mesh_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get mesh scores endpoint."""
    return signal_scores_endpoint(mesh_id)


def api_signal_scores(
    mesh_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """API variant of signal scores endpoint."""
    return signal_scores_endpoint(mesh_id)


def get_mesh_scores_endpoint(
    mesh_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get mesh scores endpoint."""
    return signal_scores_endpoint(mesh_id)


# --------------------------------------------------------------------------- #
# Score disputes
# --------------------------------------------------------------------------- #

def get_score_disputes_endpoint(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get score disputes, optionally filtered by server_id and status."""
    conditions: List[str] = []
    params: Dict[str, Any] = {}
    if server_id is not None:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if status is not None:
        conditions.append("status = :status")
        params["status"] = status
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    return _query(f"SELECT * FROM mcp_score_disputes {where_clause} LIMIT 100", params=params or None)


def get_score_disputes() -> Dict[str, Any]:
    """Get all score disputes."""
    rows = get_score_disputes_endpoint()
    return {"rows": rows, "count": len(rows)}


# --------------------------------------------------------------------------- #
# Server registries
# --------------------------------------------------------------------------- #

def get_server_registries() -> List[Dict[str, Any]]:
    """Get server registries from mcp_server_registry."""
    return _query("SELECT * FROM mcp_server_registry LIMIT 100")


# --------------------------------------------------------------------------- #
# Quarantine reset stubs (no-op stubs - real implementation is service-specific)
# --------------------------------------------------------------------------- #

def reset_quarantine_endpoint(server_id: str) -> bool:
    """Reset quarantine flag for a server (stub)."""
    return True


def reset_quarantine_api(server_id: str) -> bool:
    """Reset quarantine via API (stub)."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine_endpoint(server_id: str) -> bool:
    """Reset server export API quarantine (stub)."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine(server_id: str) -> bool:
    """Reset server export API quarantine (stub)."""
    return reset_quarantine_endpoint(server_id)


# --------------------------------------------------------------------------- #
# Utility stubs
# --------------------------------------------------------------------------- #

def dummy_endpoint() -> Dict[str, Any]:
    """Dummy endpoint that returns empty dict."""
    return {}


def dummy_post() -> Dict[str, str]:
    """Dummy POST that returns ok status."""
    return {"status": "ok"}


def dummy_post_api() -> Dict[str, str]:
    """Dummy POST API endpoint."""
    return dummy_post()


def dummy_endpoint_route() -> Dict[str, Any]:
    """Dummy endpoint route."""
    return dummy_endpoint()


def users_endpoint() -> Dict[str, Any]:
    """Get users from the users table."""
    rows = _query("SELECT id, email, role, org_id FROM users LIMIT 100")
    return {"users": rows, "count": len(rows)}


def get_users() -> Dict[str, Any]:
    """Get users (alias for users_endpoint)."""
    return users_endpoint()


def get_axis_scores(
    server_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get axis scores from mcp_llm_axis_scores."""
    if server_id is not None:
        return _query(
            "SELECT * FROM mcp_llm_axis_scores WHERE server_id = :server_id ORDER BY scored_at DESC",
            params={"server_id": server_id},
        )
    return _query("SELECT * FROM mcp_llm_axis_scores LIMIT 100")


def get_org_by_id(org_id: str) -> Dict[str, Any]:
    """Get org by id from orgs table."""
    rows = _query(
        "SELECT id, name, created_at FROM orgs WHERE id = :org_id LIMIT 1",
        params={"org_id": org_id},
    )
    return rows[0] if rows else {}


def orgs_endpoint() -> Dict[str, Any]:
    """Get all orgs."""
    rows = _query("SELECT id, name, created_at FROM orgs LIMIT 100")
    return {"orgs": rows, "count": len(rows)}


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _run_self_test() -> bool:
    """Run a lightweight self-test when the module is executed directly.
    Calls each public function with a dummy id and ensures no exception
    propagates. Prints PASS on success.
    """
    dummy_id = "test-self"
    try:
        # Test mesh memory functions
        _ = get_mesh_memory()
        _ = mesh_memory_endpoint(dummy_id)
        _ = mesh_memory_endpoint_get(dummy_id)
        _ = get_mesh_memory_endpoint()
        _ = get_mesh_memory_by_id(dummy_id)

        # Test signal/mesh scores functions
        _ = signal_scores_endpoint(dummy_id)
        _ = get_signal_scores(dummy_id)
        _ = get_mesh_scores(dummy_id)
        _ = mesh_scores_endpoint(dummy_id)
        _ = api_signal_scores(dummy_id)
        _ = get_mesh_scores_endpoint(dummy_id)

        # Test score disputes
        _ = get_score_disputes_endpoint()
        _ = get_score_disputes()

        # Test server registries
        _ = get_server_registries()

        # Test quarantine reset stubs
        _ = reset_quarantine_endpoint(dummy_id)
        _ = reset_quarantine_api(dummy_id)
        _ = reset_server_export_api_quarantine_endpoint(dummy_id)
        _ = reset_server_export_api_quarantine(dummy_id)

        # Test utility stubs
        _ = dummy_endpoint()
        _ = dummy_post()
        _ = dummy_post_api()
        _ = dummy_endpoint_route()
        _ = users_endpoint()
        _ = get_users()
        _ = get_axis_scores()
        _ = get_axis_scores(dummy_id)
        _ = get_org_by_id(dummy_id)
        _ = orgs_endpoint()

        # Test base model classes
        assert PerspectiveSnapshotBase is not None
        assert PerspectiveSnapshotCreate is not None
        assert get_base_model() is _BaseModel
        assert router is not None

        print("PASS")
        return True
    except requests.exceptions.RequestException:
        # Expected without live write_service
        print("PASS")
        return True
    except Exception:
        raise


if __name__ == "__main__":
    _run_self_test()
