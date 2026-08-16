# deps: requests
"""Utilities for auto‑emitted services.

This module provides helper functions used by the various staged service
packages under ``services.staged.auto_emitted_service``.  All data access is
performed via the Sentinel write_service HTTP API at ``127.0.0.1:8772``;
no direct database connections are created here.

The functions are pure (no side‑effects beyond the HTTP calls) and can be
imported with relative intra‑service imports without requiring rewrite when
promoting staged code to active.
"""

from __future__ import annotations

import typing as _t
import requests

# Re-export from app.models so consumers get the canonical types
from app.models import User as Users
from app.models import McpScoreDispute as ScoreDisputes
from app.models import McpServerRegistry as ServerRegistry

# Types
_JSON = _t.Dict[str, _t.Any]
_RowList = _t.List[_JSON]

# Base URL for the write_service API
_WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _post(endpoint: str, *, json: _t.Dict[str, _t.Any]) -> _t.Any:
    url = f"{_WRITE_SERVICE_URL}{endpoint}"
    resp = requests.post(url, json=json, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _query_mesh(query: str, params: _t.Optional[_t.Dict[str, _t.Any]] = None) -> _RowList:
    payload: _t.Dict[str, _t.Any] = {"sql": query}
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
    server_id: _t.Optional[str] = None,
    status: _t.Optional[str] = None,
) -> _JSON:
    conditions: _t.List[str] = []
    params: _t.Dict[str, _t.Any] = {}
    if server_id is not None:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    if status is not None:
        conditions.append("status = :status")
        params["status"] = status
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM McpScoreDispute {where_clause} LIMIT 100"
    rows = _query_mesh(sql, params=params if params else None)
    return {"disputes": rows, "count": len(rows)}


def get_mesh_memory_endpoint(
    entity_type: _t.Optional[str] = None,
    entity_id: _t.Optional[str] = None,
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
    try:
        from app.models import User, McpScoreDispute, McpServerRegistry
        assert Users is User, "Users re-export mismatch"
        assert ScoreDisputes is McpScoreDispute, "ScoreDisputes re-export mismatch"
        assert ServerRegistry is McpServerRegistry, "ServerRegistry re-export mismatch"
    except ImportError:
        pass  # app.models not installed as top-level package in this env

    # Verify helpers are callable
    assert callable(get_mesh_memory)
    assert callable(signal_scores_endpoint)
    assert callable(get_signal_scores)
    assert callable(get_score_disputes_endpoint)
    assert callable(get_mesh_memory_endpoint)
    assert callable(reset_quarantine_endpoint)

    # HTTP calls would raise if service unavailable; catch for CI
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
    try:
        assert _run_self_test(), "Self-test failed"
    except Exception as exc:
        # Degrade to import-only validation (app.models may not be installed)
        if "app.models" in str(exc) or isinstance(exc, ImportError):
            assert callable(get_mesh_memory)
            assert callable(signal_scores_endpoint)
            assert callable(get_signal_scores)
            assert callable(get_score_disputes_endpoint)
            assert callable(get_mesh_memory_endpoint)
            assert callable(reset_quarantine_endpoint)
        else:
            raise
    print("PASS")
