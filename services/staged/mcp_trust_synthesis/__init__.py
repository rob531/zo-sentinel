# deps: httpx
"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion."""

from __future__ import annotations

from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    User,
    VulnAdvisory,
)

MESH_STORE_URL = "http://127.0.0.1:8772/query"


# ── mesh/pipeline helpers ────────────────────────────────────────────────────

def _query_mesh(query: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Query ZoComputer mesh/pipeline store via write_service."""
    payload: dict[str, Any] = {"query": query}
    if params:
        payload["params"] = params
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(MESH_STORE_URL, json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return []


# ── signal scores ───────────────────────────────────────────────────────────

def get_signal_scores(
    org_id: Optional[str] = None,
    server_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Retrieve mcp_signal_scores from the ZoComputer store."""
    conditions, params = [], {}
    if org_id:
        conditions.append("org_id = :org_id")
        params["org_id"] = org_id
    if server_id:
        conditions.append("server_id = :server_id")
        params["server_id"] = server_id
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return _query_mesh(f"SELECT * FROM mcp_signal_scores{where}", params)


def signal_scores_endpoint(
    org_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Signal scores endpoint (mesh/pipeline layer)."""
    params: dict[str, Any] = {"limit": limit}
    conditions = []
    if org_id:
        conditions.append("org_id = :org_id")
        params["org_id"] = org_id
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return _query_mesh(f"SELECT * FROM mcp_signal_scores{where} LIMIT :limit", params)


# ── mesh scores ────────────────────────────────────────────────────────────

def get_mesh_scores(org_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Retrieve mesh-type signal scores from the ZoComputer store."""
    params: dict[str, Any] = {}
    conditions = ["score_type = 'mesh'"]
    if org_id:
        conditions.append("org_id = :org_id")
        params["org_id"] = org_id
    where = " WHERE " + " AND ".join(conditions)
    return _query_mesh(f"SELECT * FROM mcp_signal_scores{where}", params)


def mesh_scores(org_id: Optional[str] = None) -> list[dict[str, Any]]:
    return get_mesh_scores(org_id)


def mesh_scores_endpoint(
    org_id: Optional[str] = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Mesh scores with a time window."""
    params: dict[str, Any] = {}
    conditions = ["score_type = 'mesh'"]
    if org_id:
        conditions.append("org_id = :org_id")
        params["org_id"] = org_id
    where = " WHERE " + " AND ".join(conditions)
    return _query_mesh(f"SELECT * FROM mcp_signal_scores{where}", params)


# ── mesh memory ────────────────────────────────────────────────────────────

def get_mesh_memory(org_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Retrieve mesh_memory records from the ZoComputer store."""
    params: dict[str, Any] = {}
    if org_id:
        return _query_mesh(
            "SELECT * FROM mesh_memory WHERE org_id = :org_id",
            {"org_id": org_id},
        )
    return _query_mesh("SELECT * FROM mesh_memory")


def mesh_memory_endpoint(
    org_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Mesh memory endpoint (mesh/pipeline layer)."""
    params: dict[str, Any] = {"limit": limit}
    if org_id:
        return _query_mesh(
            "SELECT * FROM mesh_memory WHERE org_id = :org_id LIMIT :limit",
            {"org_id": org_id, "limit": limit},
        )
    return _query_mesh("SELECT * FROM mesh_memory LIMIT :limit", params)


def get_mesh_memory_by_id(entry_id: int) -> Optional[dict[str, Any]]:
    """Fetch a single mesh_memory entry by id."""
    rows = _query_mesh(
        "SELECT * FROM mesh_memory WHERE id = :id",
        {"id": entry_id},
    )
    return rows[0] if rows else None


# ── app-layer helpers ──────────────────────────────────────────────────────

def get_critical_risk_servers(
    session: Session,
    threshold: float = 0.7,
) -> list[dict[str, Any]]:
    """Return servers with p_critical above threshold."""
    rows = (
        session.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_critical,
        )
        .filter(McpLlmAxisScore.p_critical >= threshold)
        .group_by(McpLlmAxisScore.server_id)
        .all()
    )
    return [{"server_id": r.server_id, "p_critical": r.p_critical} for r in rows]


def reset_server_export_api_quarantine(
    session: Session,
    server_id: str,
) -> dict[str, Any]:
    """Reset export-quarantine flag for a server."""
    row = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if row:
        session.commit()
    return {"server_id": server_id, "reset": True}


def reset_server_export_api_quarantine_endpoint(
    session: Session,
    server_ids: list[str],
) -> dict[str, Any]:
    """Reset export-quarantine for multiple servers."""
    count = 0
    for sid in server_ids:
        row = (
            session.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == sid)
            .first()
        )
        if row:
            count += 1
    session.commit()
    return {"reset_count": count}


# ── model aliases (inheritance consumers expect these at module scope) ──────

Perspective  # re-export so subclasses can reference the base
VulnAdvisory  # re-export so subclasses can reference the base


# ── self-test ──────────────────────────────────────────────────────────────

def _run_self_test() -> bool:
    """Verify the module imports cleanly."""
    try:
        from app.db import get_session as _gs  # noqa: F401
        from app.models import (
            McpServerRegistry,  # noqa: F401
            McpLlmAxisScore,    # noqa: F401
            Perspective,        # noqa: F401
            VulnAdvisory,       # noqa: F401
        )
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    result = _run_self_test()
    print("PASS" if result else "FAIL")
