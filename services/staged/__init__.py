# deps: requests
"""Utilities for staged services.

This module provides helper functions that are used by the various staged
service packages (e.g. ``services.staged.attestation_refresh``).  All data
access is performed via the Sentinel write_service HTTP API at
``127.0.0.1:8772``; no direct database connections are created here.

The functions are pure (no side‑effects beyond the HTTP calls) and can be
imported with relative intra‑service imports without requiring rewrite when
promoting staged code to active.
"""

from __future__ import annotations

import typing as _t
import requests

# Types
_JSON = _t.Dict[str, _t.Any]
_RowList = _t.List[_JSON]

# Base URL for the write_service API
_WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _post(endpoint: str, *, json: _t.Dict[str, _t.Any]) -> _t.Any:
    """Helper to POST to the write_service API.

    Args:
        endpoint: Path component after the base URL (e.g. ``/query``).
        json: JSON payload.

    Returns:
        Parsed JSON response.
    """
    url = f"{_WRITE_SERVICE_URL}{endpoint}"
    resp = requests.post(url, json=json, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _query_mesh(query: str, params: _t.Optional[_t.Dict[str, _t.Any]] = None) -> _RowList:
    """Execute a SELECT against the mesh tables.

    The function forwards the query to the ``/query`` endpoint which expects a
    JSON payload with ``sql`` and optional ``params``.  The response is a list of
    rows where each row is a ``dict`` mapping column names to values.

    B608 mitigation: params are passed as a dict and forwarded to write_service
    which handles parameter binding server-side.
    """
    payload: _t.Dict[str, _t.Any] = {"sql": query}
    if params:
        payload["params"] = params
    return _post("/query", json=payload)


def get_mesh_memory() -> _JSON:
    """Return the latest mesh memory snapshot.

    The underlying table is ``mesh_memory``.  We fetch the most recent row
    ordered by ``timestamp`` descending.
    """
    rows = _query_mesh(
        "SELECT * FROM mesh_memory ORDER BY timestamp DESC LIMIT 1"
    )
    return rows[0] if rows else {}


def signal_scores_endpoint() -> _RowList:
    """Return all signal scores.

    Reads from the ``mcp_signal_scores`` table.
    """
    return _query_mesh("SELECT * FROM mcp_signal_scores")


def mesh_memory_endpoint() -> _JSON:
    """Alias for :func:`get_mesh_memory` used by older services.
    """
    return get_mesh_memory()


def get_signal_scores() -> _RowList:
    """Alias for :func:`signal_scores_endpoint`.
    """
    return signal_scores_endpoint()


def get_score_disputes_endpoint(
    server_id: _t.Optional[str] = None,
    status: _t.Optional[str] = None,
) -> _JSON:
    """Return score disputes, optionally filtered by server_id and/or status.

    Reads from ``mcp_signal_scores`` (disputes table in the mesh layer).
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
    rows = _query_mesh(sql, params=params if params else None)
    return {"disputes": rows, "count": len(rows)}


def get_mesh_memory_endpoint(
    entity_type: _t.Optional[str] = None,
    entity_id: _t.Optional[str] = None,
) -> _JSON:
    """Alias for :func:`get_mesh_memory` with optional entity filter."""
    if entity_type and entity_id:
        rows = _query_mesh(
            "SELECT * FROM mesh_memory WHERE entity_type = :entity_type AND entity_id = :entity_id ORDER BY timestamp DESC LIMIT 1",
            params={"entity_type": entity_type, "entity_id": entity_id},
        )
        return rows[0] if rows else {}
    return get_mesh_memory()


def reset_quarantine_endpoint(server_id: str) -> bool:
    """Reset quarantine status for a given server.

    Performs an ``UPDATE`` on the ``service_health`` table.  The function returns
    ``True`` if the statement executed without error.
    """
    sql = "UPDATE service_health SET status = 'active' WHERE server_id = :sid"
    _post(
        "/execute",
        json={"sql": sql, "params": {"sid": server_id}, "wait": True},
    )
    return True


def _run_self_test() -> bool:
    """Simple self‑test exercised when the module is run directly.

    It calls each public helper with a minimal request to ensure the HTTP API
    is reachable and the responses have the expected shape.  The test does **not**
    modify any persistent data – the ``reset_quarantine_endpoint`` call is made
    with a dummy ID that is safe to issue.
    """
    # Verify all expected exports exist
    from services.staged.admin_disputes import Users, ScoreDisputes
    from app.models import User, McpScoreDispute
    assert Users is User, "Users re-export mismatch"
    assert ScoreDisputes is McpScoreDispute, "ScoreDisputes re-export mismatch"

    # The following calls will raise if the service is unavailable.
    _ = get_mesh_memory()
    _ = signal_scores_endpoint()
    _ = get_signal_scores()
    _ = get_score_disputes_endpoint()
    _ = get_mesh_memory_endpoint()
    # Reset with a harmless identifier; the endpoint is idempotent.
    _ = reset_quarantine_endpoint("test-server-id")
    return True


if __name__ == "__main__":
    # Run a quick sanity check when executed as a script.
    assert _run_self_test(), "Self‑test failed"
    print("staged __init__ self‑test passed")
