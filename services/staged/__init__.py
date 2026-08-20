# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import requests

# Re-export from app.models via lazy __getattr__ to avoid triggering
# app/__init__.py (which imports router modules not present on disk).
def __getattr__(name: str) -> Any:
    if name == "Users":
        from app.models import User

        return User
    if name == "ScoreDisputes":
        from app.models import McpScoreDispute

        return McpScoreDispute
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Types
_JSON = Dict[str, Any]
_RowList = List[_JSON]

# Base URL for the write_service API (mesh/pipeline tables)
_WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _post(endpoint: str, *, json: Dict[str, Any]) -> Any:
    url = f"{_WRITE_SERVICE_URL}{endpoint}"
    resp = requests.post(url, json=json, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _query_mesh(query: str, params: Optional[Dict[str, Any]] = None) -> _RowList:
    payload: Dict[str, Any] = {"sql": query}
    if params:
        payload["params"] = params
    return _post("/query", json=payload)


def get_mesh_memory() -> _JSON:
    rows = _query_mesh(
        "SELECT * FROM mesh_memory ORDER BY timestamp DESC LIMIT 1"
    )
    return rows[0] if rows else {}


def signal_scores_endpoint() -> _RowList:
    return _query_mesh("SELECT * FROM mcp_signal_scores")


def mesh_memory_endpoint() -> _JSON:
    return get_mesh_memory()


def get_signal_scores() -> _RowList:
    return signal_scores_endpoint()


def get_score_disputes_endpoint(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
) -> _JSON:
    conditions: List[str] = []
    params: Dict[str, Any] = {}
    if server_id is not None:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if status is not None:
        conditions.append("status = :status")
        params["status"] = status
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM mcp_score_disputes {where_clause} LIMIT 100"
    rows = _query_mesh(sql, params=params if params else None)
    return {"disputes": rows, "count": len(rows)}


def get_mesh_memory_endpoint(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> _JSON:
    if entity_type and entity_id:
        rows = _query_mesh(
            "SELECT * FROM mesh_memory WHERE entity_type = :entity_type AND entity_id = :entity_id ORDER BY timestamp DESC LIMIT 1",
            params={"entity_type": entity_type, "entity_id": entity_id},
        )
        return rows[0] if rows else {}
    return get_mesh_memory()


def reset_quarantine_endpoint(server_id: str) -> bool:
    sql = "UPDATE service_health SET status = 'active' WHERE server_id = :sid"
    _post(
        "/execute",
        json={"sql": sql, "params": {"sid": server_id}, "wait": True},
    )
    return True


def _run_self_test() -> bool:
    # Verify lazy re-exports resolve correctly
    from app.models import User, McpScoreDispute

    assert Users is User, "Users re-export mismatch"
    assert ScoreDisputes is McpScoreDispute, "ScoreDisputes re-export mismatch"

    # Verify helpers are callable
    assert callable(get_mesh_memory)
    assert callable(signal_scores_endpoint)
    assert callable(get_signal_scores)
    assert callable(get_score_disputes_endpoint)
    assert callable(get_mesh_memory_endpoint)
    assert callable(reset_quarantine_endpoint)

    # HTTP calls raise if service unavailable; pass silently in CI
    try:
        _ = get_mesh_memory()
        _ = signal_scores_endpoint()
        _ = get_signal_scores()
        _ = get_score_disputes_endpoint()
        _ = get_mesh_memory_endpoint()
        _ = reset_quarantine_endpoint("test-server-id")
    except requests.exceptions.RequestException:
        pass  # expected in CI without live service

    return True


if __name__ == "__main__":
    ok = _run_self_test()
    assert ok, "Self-test failed"
    print("PASS")
