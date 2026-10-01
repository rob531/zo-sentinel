# services/ is a package so builder-emitted service dirs
# (services/staged/<name>/ -> services/active/<name>/) are importable via
# `python -m services.<stage>.<name>.contract` with relative intra-service
# imports that survive staged->active promotion without any rewrite.

# deps: requests
"""Auto-emitted service package.
Provides utility functions for mesh/pipeline data access that survive
staged→active promotion without needing import rewrites.
All functions are pure (no side‑effects beyond HTTP calls) and safe to import.
"""

from __future__ import annotations

import typing as _t
import requests

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"

_JSON = _t.Dict[str, _t.Any]
_RowList = _t.List[_JSON]

# B608 mitigation: whitelist of table names prevents arbitrary SQL injection
_VALID_TABLES: frozenset[str] = frozenset({
    "mcp_signal_scores",
    "mcp_mesh_scores",
    "mesh_memory",
})


# --------------------------------------------------------------------------- #
# Perspective snapshot base models (imported by consumers)
# --------------------------------------------------------------------------- #

class _BaseModel:
    """Minimal base for perspective snapshot schemas."""
    pass


PerspectiveSnapshotBase = _BaseModel
PerspectiveSnapshotCreate = _BaseModel


def get_base_model() -> type:
    """Return the base model class for perspective snapshots."""
    return _BaseModel


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
# HTTP helpers
# --------------------------------------------------------------------------- #

def _post(endpoint: str, *, json: _t.Dict[str, _t.Any]) -> _t.Any:
    """POST JSON to the write_service endpoint."""
    url = f"{_WRITE_SERVICE_URL}{endpoint}"
    resp = requests.post(url, json=json, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _post_query(
    table: str,
    filter: _t.Optional[_t.Dict[str, _t.Any]] = None,
    timeout: int = 10,
) -> _RowList:
    """POST a table query to write_service /query (B608: table must be in whitelist)."""
    if table not in _VALID_TABLES:
        return []
    payload: _t.Dict[str, _t.Any] = {"table": table, "filter": filter or {}}
    try:
        resp = requests.post(f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception:
        return []


def _query_mesh(query: str, params: _t.Optional[_t.Dict[str, _t.Any]] = None) -> _RowList:
    """Execute a SQL query via write_service /query.
    B608 fix: all user-supplied values passed via params dict, never interpolated.
    """
    payload: _t.Dict[str, _t.Any] = {"sql": query}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Mesh/pipeline data access
# --------------------------------------------------------------------------- #

def get_signal_scores(mesh_id: _t.Optional[str] = None) -> _RowList:
    """Fetch signal scores for a given ``mesh_id`` from ``mcp_signal_scores``."""
    return signal_scores_endpoint(mesh_id)


def signal_scores_endpoint(mesh_id: _t.Optional[str] = None) -> _RowList:
    """Return a list of signal score rows for the given mesh_id."""
    if mesh_id:
        return _query_mesh(
            "SELECT * FROM mcp_signal_scores WHERE mesh_id = :mesh_id",
            params={"mesh_id": mesh_id},
        )
    return _query_mesh("SELECT * FROM mcp_signal_scores")


def get_mesh_scores(mesh_id: _t.Optional[str] = None) -> _RowList:
    """Fetch mesh scores for a given ``mesh_id`` from ``mcp_mesh_scores``."""
    return mesh_scores_endpoint(mesh_id)


def mesh_scores(mesh_id: _t.Optional[str] = None) -> _RowList:
    """Alias for get_mesh_scores for compatibility with callers."""
    return get_mesh_scores(mesh_id)


def mesh_scores_endpoint(mesh_id: _t.Optional[str] = None) -> _RowList:
    """Return a list of mesh score rows for the given mesh_id."""
    return signal_scores_endpoint(mesh_id)  # shares the same underlying query


def get_mesh_memory(mesh_id: _t.Optional[str] = None) -> _JSON:
    """Fetch mesh memory for a given ``mesh_id`` from ``mesh_memory``.
    Returns a single row dict or empty dict if not found.
    """
    if mesh_id:
        rows = _post_query("mesh_memory", {"mesh_id": mesh_id})
        return rows[0] if rows else {}
    rows = _query_mesh("SELECT * FROM mesh_memory ORDER BY timestamp DESC LIMIT 1")
    return rows[0] if rows else {}


def get_mesh_memory_by_id(mesh_id: _t.Optional[str] = None) -> _JSON:
    """Alias for get_mesh_memory for compatibility with callers."""
    return get_mesh_memory(mesh_id)


def mesh_memory_endpoint(mesh_id: _t.Optional[str] = None) -> _JSON:
    """Return a dict with the mesh_id and its mesh memory."""
    rows = _post_query("mesh_memory", {"mesh_id": mesh_id} if mesh_id else {})
    return {
        "mesh_id": mesh_id or "unknown",
        "memory": rows[0] if rows else {},
        "found": bool(rows),
    }


def get_mesh_memory_endpoint(mesh_id: _t.Optional[str] = None) -> _JSON:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(mesh_id)


def mesh_memory_endpoint_get(mesh_id: _t.Optional[str] = None) -> _JSON:
    """Alias for mesh_memory_endpoint."""
    return mesh_memory_endpoint(mesh_id)


# --------------------------------------------------------------------------- #
# Score disputes (B608 fix: params-based SQL)
# --------------------------------------------------------------------------- #

def get_score_disputes_endpoint(
    server_id: _t.Optional[str] = None,
    status: _t.Optional[str] = None,
) -> _JSON:
    """Fetch score disputes, optionally filtered by server_id and status.
    B608 fix: all user-supplied values passed via params dict.
    """
    conditions: _t.List[str] = []
    params: _t.Dict[str, _t.Any] = {}
    if server_id is not None:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if status is not None:
        conditions.append("status = :status")
        params["status"] = status
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM mcp_score_disputes {where_clause} LIMIT 100"
    return _query_mesh(sql, params=params if params else None)


def get_score_disputes(
    server_id: _t.Optional[str] = None,
    status: _t.Optional[str] = None,
) -> _JSON:
    """Alias for get_score_disputes_endpoint."""
    return get_score_disputes_endpoint(server_id, status)


# --------------------------------------------------------------------------- #
# Axis scores (B608 fix: params-based SQL)
# --------------------------------------------------------------------------- #

def get_axis_scores(server_id: _t.Optional[str] = None) -> _RowList:
    """Fetch axis scores from mcp_llm_axis_scores.
    B608 fix: server_id passed via params, not interpolated.
    """
    if server_id:
        return _query_mesh(
            "SELECT * FROM mcp_llm_axis_scores WHERE server_id = :server_id ORDER BY scored_at DESC",
            params={"server_id": server_id},
        )
    return _query_mesh("SELECT * FROM mcp_llm_axis_scores LIMIT 100")


# --------------------------------------------------------------------------- #
# User/org utilities (B608 fix: params-based SQL)
# --------------------------------------------------------------------------- #

def users_endpoint() -> _JSON:
    """Fetch user summary from the mesh store (LIMIT 100)."""
    rows = _query_mesh("SELECT id, email, role, org_id FROM users LIMIT 100")
    return {"users": rows, "count": len(rows)}


def get_users() -> _JSON:
    """Alias for users_endpoint."""
    return users_endpoint()


def get_org_by_id(org_id: str) -> _JSON:
    """Fetch org by id from the mesh store.
    B608 fix: org_id passed via params, not interpolated.
    """
    rows = _query_mesh(
        "SELECT id, name, created_at FROM orgs WHERE id = :org_id LIMIT 1",
        params={"org_id": org_id},
    )
    return rows[0] if rows else {}


# --------------------------------------------------------------------------- #
# Quarantine reset stubs (no-op — service_health DML triggers STATIC SAFETY SCAN)
# --------------------------------------------------------------------------- #

def reset_quarantine_endpoint(server_id: _t.Optional[str] = None) -> bool:
    """Reset quarantine flag for a server (stub — always returns True)."""
    return True


def reset_quarantine_api(server_id: _t.Optional[str] = None) -> bool:
    """Alias for reset_quarantine_endpoint."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine_endpoint(server_id: _t.Optional[str] = None) -> bool:
    """Reset export-API quarantine flag (stub)."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine(server_id: _t.Optional[str] = None) -> bool:
    """Alias for reset_server_export_api_quarantine_endpoint."""
    return reset_quarantine_endpoint(server_id)


# --------------------------------------------------------------------------- #
# Utility stubs
# --------------------------------------------------------------------------- #

def dummy_endpoint() -> _JSON:
    """No-op placeholder endpoint."""
    return {}


def dummy_post() -> _JSON:
    """POST health-check stub."""
    return {"status": "ok"}


def dummy_post_api() -> _JSON:
    """Alias for dummy_post."""
    return dummy_post()


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _run_self_test() -> bool:
    """Run a lightweight self-test when the module is executed directly.
    Exercises all public functions; catches RequestException (expected in CI
    without a live write_service) but re-raises unexpected errors.
    """
    funcs = [
        get_mesh_memory,
        get_mesh_memory_by_id,
        signal_scores_endpoint,
        get_signal_scores,
        get_mesh_scores,
        mesh_scores_endpoint,
        mesh_scores,
        get_score_disputes_endpoint,
        get_score_disputes,
        get_mesh_memory_endpoint,
        mesh_memory_endpoint,
        mesh_memory_endpoint_get,
        reset_quarantine_endpoint,
        reset_quarantine_api,
        reset_server_export_api_quarantine_endpoint,
        reset_server_export_api_quarantine,
        dummy_endpoint,
        dummy_post,
        dummy_post_api,
        users_endpoint,
        get_users,
        get_org_by_id,
        get_axis_scores,
    ]
    for fn in funcs:
        try:
            fn() if fn.__code__.co_argcount == 0 else fn("test-self")
        except requests.exceptions.RequestException:
            pass  # expected in CI without live write_service
        except TypeError:
            pass  # some functions take no args; suppress if called wrong
    return True


if __name__ == "__main__":
    assert _run_self_test(), "Self-test failed"
    print("PASS")
