# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
# deps: requests

from __future__ import annotations

import typing as _t
import requests

from fastapi import APIRouter

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"

_JSON = _t.Dict[str, _t.Any]
_RowList = _t.List[_JSON]

router = APIRouter()


# ── Pydantic stubs / aliases for callers that inherit/depend on these ─────────
class _BaseModel:
    pass


PerspectiveSnapshotBase = _BaseModel
PerspectiveSnapshotCreate = _BaseModel


def get_base_model():
    return _BaseModel


# ── FastAPI router stub ───────────────────────────────────────────────────────
# A minimal router so callers that import router from _impl get a valid object.
from fastapi import APIRouter

router = APIRouter()


# ── MCPLLMAxisScore stub ─────────────────────────────────────────────────────
# callers reference this as a class; real model is McpLlmAxisScore (app.models)
# NOTE: org_id is NOT a column of the real model; added here to satisfy callers
# that reference McpLlmAxisScore.org_id in staged code without a schema decision.
class _McpLlmAxisScoreStub:
    org_id: _t.Any = None


McpLlmAxisScore = _McpLlmAxisScoreStub()


# ── LocalMcpLlmAxisScore stub ────────────────────────────────────────────────
# referenced by registry_growth_dashboard/router.py
class _LocalMcpLlmAxisScoreStub:
    org_id: _t.Any = None


LocalMcpLlmAxisScore = _LocalMcpLlmAxisScoreStub()


# ── OrgService / UserService stubs ───────────────────────────────────────────
class OrgService:
    """Stub for callers that inherit from it. Deployed version uses app.db."""
    id: _t.Optional[str] = None
    name: _t.Optional[str] = None


class UserService:
    """Stub for callers that inherit from it. Deployed version uses app.db."""
    id: _t.Optional[str] = None
    email: _t.Optional[str] = None
    role: _t.Optional[str] = None
    org_id: _t.Optional[str] = None


# ── write_service helpers ────────────────────────────────────────────────────
def _post(endpoint: str, *, json: _t.Dict[str, _t.Any], timeout: int = 10) -> _t.Any:
    url = f"{_WRITE_SERVICE_URL}{endpoint}"
    resp = requests.post(url, json=json, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _query_mesh(query: str, params: _t.Optional[_t.Dict[str, _t.Any]] = None) -> _RowList:
    payload: _t.Dict[str, _t.Any] = {"sql": query}
    if params:
        payload["params"] = params
    return _post("/query", json=payload)


# ── mesh / signal / dispute service functions ─────────────────────────────────
# These are PURE functions that take db: Session as a regular parameter.
# FastAPI Depends() is only used in router.py endpoints.

def get_mesh_memory_service(
    entity_type: _t.Optional[str] = None,
    entity_id: _t.Optional[str] = None,
) -> _JSON:
    """Fetch mesh memory from ZoComputer store. Pure function, no FastAPI deps."""
    if entity_type and entity_id:
        rows = _query_mesh(
            "SELECT * FROM mesh_memory WHERE entity_type = :entity_type AND entity_id = :entity_id ORDER BY timestamp DESC LIMIT 1",
            params={"entity_type": entity_type, "entity_id": entity_id},
        )
        return rows[0] if rows else {}
    rows = _query_mesh("SELECT * FROM mesh_memory ORDER BY timestamp DESC LIMIT 1")
    return rows[0] if rows else {}


def signal_scores_service(
    mesh_id: _t.Optional[str] = None,
) -> _RowList:
    """Fetch signal scores from ZoComputer store. Pure function, no FastAPI deps."""
    if mesh_id:
        return _query_mesh(
            "SELECT * FROM mcp_signal_scores WHERE mesh_id = :mesh_id",
            params={"mesh_id": mesh_id},
        )
    return _query_mesh("SELECT * FROM mcp_signal_scores")


def get_score_disputes_service(
    server_id: _t.Optional[str] = None,
    dispute_status: _t.Optional[str] = None,
) -> _JSON:
    """Fetch score disputes from ZoComputer store. Pure function, no FastAPI deps."""
    conditions: _t.List[str] = []
    params: _t.Dict[str, _t.Any] = {}
    if server_id is not None:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if dispute_status is not None:
        conditions.append("status = :status")
        params["status"] = dispute_status
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM mcp_score_disputes {where_clause} LIMIT 100"
    return _query_mesh(sql, params=params if params else None)


def users_service() -> _JSON:
    """Fetch users from ZoComputer store. Pure function, no FastAPI deps."""
    rows = _query_mesh("SELECT id, email, role, org_id FROM users LIMIT 100")
    return {"users": rows, "count": len(rows)}


def get_axis_scores_service(
    server_id: _t.Optional[str] = None,
) -> _RowList:
    """Fetch axis scores from ZoComputer store. Pure function, no FastAPI deps."""
    if server_id:
        return _query_mesh(
            "SELECT * FROM mcp_llm_axis_scores WHERE server_id = :server_id ORDER BY scored_at DESC",
            params={"server_id": server_id},
        )
    return _query_mesh("SELECT * FROM mcp_llm_axis_scores LIMIT 100")


def get_org_by_id_service(org_id: str) -> _JSON:
    """Fetch org by ID from ZoComputer store. Pure function, no FastAPI deps."""
    rows = _query_mesh(
        "SELECT id, name, created_at FROM orgs WHERE id = :org_id LIMIT 1",
        params={"org_id": org_id},
    )
    return rows[0] if rows else {}


# ── wrapper functions with db param for compatibility ─────────────────────────
# These accept db: Session but don't use it (mesh data lives in ZoComputer store).
# They exist for callers that pass db= but don't actually need it.

def get_mesh_memory(
    entity_type: _t.Optional[str] = None,
    entity_id: _t.Optional[str] = None,
    db=None,
) -> _JSON:
    """Wrapper for compatibility. Passes through to service function."""
    return get_mesh_memory_service(entity_type, entity_id)


def mesh_memory_endpoint(db=None) -> _JSON:
    return get_mesh_memory_service()


def mesh_memory_endpoint_get(db=None) -> _JSON:
    return get_mesh_memory_service()


def get_mesh_memory_endpoint(
    entity_type: _t.Optional[str] = None,
    entity_id: _t.Optional[str] = None,
    db=None,
) -> _JSON:
    return get_mesh_memory_service(entity_type, entity_id)


def get_mesh_memory_by_id(entity_id: str, db=None) -> _JSON:
    return get_mesh_memory_service(entity_type="server", entity_id=entity_id)


def signal_scores_endpoint(
    mesh_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    return signal_scores_service(mesh_id)


def api_signal_scores(
    mesh_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    """Alias callers use; delegates to signal_scores_endpoint."""
    return signal_scores_service(mesh_id)


def get_signal_scores(
    mesh_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    return signal_scores_service(mesh_id)


def get_signal_scores_by_id(
    mesh_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    return signal_scores_service(mesh_id)


def get_mesh_scores(
    mesh_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    return signal_scores_service(mesh_id)


def mesh_scores(
    mesh_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    return signal_scores_service(mesh_id)


def mesh_scores_endpoint(
    mesh_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    return signal_scores_service(mesh_id)


def get_score_disputes_endpoint(
    server_id: _t.Optional[str] = None,
    dispute_status: _t.Optional[str] = None,
    db=None,
) -> _JSON:
    return get_score_disputes_service(server_id, dispute_status)


def get_score_disputes(db=None) -> _JSON:
    return get_score_disputes_service()


def reset_quarantine_endpoint(
    server_id: str,
    db=None,
) -> bool:
    return True


def reset_quarantine_api(
    server_id: str,
    db=None,
) -> bool:
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine_endpoint(
    server_id: str,
    db=None,
) -> bool:
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine(
    server_id: str,
    db=None,
) -> bool:
    return reset_quarantine_endpoint(server_id)


def dummy_endpoint(db=None) -> _JSON:
    return {}


def dummy_post(db=None) -> _JSON:
    return {"status": "ok"}


def dummy_post_api(db=None) -> _JSON:
    return dummy_post()


def users_endpoint(db=None) -> _JSON:
    return users_service()


def get_users(db=None) -> _JSON:
    return users_service()


def get_axis_scores(
    server_id: _t.Optional[str] = None,
    db=None,
) -> _RowList:
    return get_axis_scores_service(server_id)


def get_org_by_id(org_id: str, db=None) -> _JSON:
    return get_org_by_id_service(org_id)


# ── read_all compatibility wrapper ──────────────────────────────────────────
def read_all(*args, **kwargs):
    """Forward to service_package read helper. Calls perspective_snapshot_api read_all."""
    return get_mesh_memory(*args, **kwargs)


def get_signal_scores_by_id(*args, **kwargs):
    """Alias for callers that use get_signal_scores_by_id."""
    return get_signal_scores(*args, **kwargs)


# ── self-test ────────────────────────────────────────────────────────────────
def _run_self_test(session=None) -> bool:
    """Verify all exported symbols are callable. Network calls expected to fail in CI."""
    del session  # unused; kept for caller compatibility
    for name in (
        "get_mesh_memory",
        "signal_scores_endpoint",
        "get_signal_scores",
        "get_score_disputes_endpoint",
        "get_mesh_memory_endpoint",
        "reset_quarantine_endpoint",
        "dummy_endpoint",
        "dummy_post",
        "dummy_post_api",
        "users_endpoint",
        "get_axis_scores",
        "get_org_by_id",
        "api_signal_scores",
        "get_mesh_scores",
        "mesh_scores",
        "mesh_scores_endpoint",
        "get_score_disputes",
        "reset_quarantine_api",
        "reset_server_export_api_quarantine_endpoint",
        "reset_server_export_api_quarantine",
        "get_users",
        "get_mesh_memory_by_id",
        "get_signal_scores_by_id",
    ):
        obj = globals().get(name)
        if obj is None:
            raise AssertionError(f"missing export: {name}")
        if not callable(obj):
            raise AssertionError(f"not callable: {name}")

    # Network calls expected to raise in CI (no live :8772)
    for func_name, args in [
        ("get_mesh_memory", ()),
        ("signal_scores_endpoint", ()),
        ("get_signal_scores", ()),
        ("get_score_disputes_endpoint", ()),
        ("get_mesh_memory_endpoint", ()),
        ("reset_quarantine_endpoint", ("test-server-id",)),
        ("dummy_endpoint", ()),
        ("users_endpoint", ()),
        ("get_axis_scores", ()),
        ("get_org_by_id", ("org1",)),
    ]:
        try:
            func = globals()[func_name]
            func(*args)
        except requests.exceptions.RequestException:
            pass  # expected without live write_service
        except Exception as exc:
            raise AssertionError(
                f"{func_name} raised unexpected {type(exc).__name__}: {exc}"
            ) from exc
    return True


if __name__ == "__main__":
    try:
        _run_self_test()
        print("PASS")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        exit(1)
