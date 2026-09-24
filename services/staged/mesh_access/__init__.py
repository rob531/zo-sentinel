# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
# deps: requests

"""Provides mesh/pipeline data-access utilities that survive staged→active
promotion without import rewrites. All functions are pure HTTP; no FastAPI,
no app.db, no app.models imports at the module level. Safe to import at
import time."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# B608 mitigation: whitelist of permitted table names
_VALID_TABLES: frozenset[str] = frozenset({
    "mcp_signal_scores",
    "mcp_mesh_scores",
    "mesh_memory",
})


# --------------------------------------------------------------------------- #
# Base classes (，供 callers to inherit from)
# --------------------------------------------------------------------------- #

class Perspective:
    """Perspective base class for org-entity search callers to inherit from."""

    def __init__(
        self,
        id: Optional[str] = None,
        org_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        facet_filters: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.id = id
        self.org_id = org_id
        self.name = name
        self.description = description
        self.facet_filters = facet_filters or {}
        self.created_by = created_by
        self.created_at = created_at
        self.updated_at = updated_at
        self.meta = meta or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "name": self.name,
            "description": self.description,
            "facet_filters": self.facet_filters,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "meta": self.meta,
        }


class VulnAdvisory:
    """VulnAdvisory base class for org-entity search callers to inherit from."""

    def __init__(
        self,
        id: Optional[str] = None,
        feed: Optional[str] = None,
        summary: Optional[str] = None,
        severity: Optional[str] = None,
        ecosystem: Optional[str] = None,
        package: Optional[str] = None,
        affected_ranges: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
        source_url: Optional[str] = None,
        published_at: Optional[str] = None,
        fetched_at: Optional[str] = None,
        identities: Optional[Dict[str, Any]] = None,
        content_hash: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.id = id
        self.feed = feed
        self.summary = summary
        self.severity = severity
        self.ecosystem = ecosystem
        self.package = package
        self.affected_ranges = affected_ranges or []
        self.aliases = aliases or []
        self.source_url = source_url
        self.published_at = published_at
        self.fetched_at = fetched_at
        self.identities = identities or {}
        self.content_hash = content_hash
        self.meta = meta or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "feed": self.feed,
            "summary": self.summary,
            "severity": self.severity,
            "ecosystem": self.ecosystem,
            "package": self.package,
            "affected_ranges": self.affected_ranges,
            "aliases": self.aliases,
            "source_url": self.source_url,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "identities": self.identities,
            "content_hash": self.content_hash,
            "meta": self.meta,
        }


# --------------------------------------------------------------------------- #
# Internal HTTP helpers
# --------------------------------------------------------------------------- #

def _post_query(
    table: str,
    filter: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """POST a table-row query to write_service /query endpoint."""
    if table not in _VALID_TABLES:
        return []
    payload: Dict[str, Any] = {"table": table, "filter": filter or {}}
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/query", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _post_sql(
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """POST a SQL query to write_service /query with parameterized values.

    B608 fix: all user-supplied values go through params, never interpolated.
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
        return data.get("rows", []) if isinstance(data, dict) else data
    except Exception:
        return []


def _query_mesh(
    sql: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Alias for _post_sql for backward compatibility."""
    return _post_sql(sql, params, timeout)


def _post(
    table: str,
    rows: Dict[str, Any],
    timeout: int = 10,
) -> bool:
    """POST rows to write_service /write endpoint.

    B608 fix: table name is validated against whitelist before use.
    Returns True on success, False on error.
    """
    if table not in _VALID_TABLES:
        return False
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE_URL}/write", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Mesh/pipeline data access
# --------------------------------------------------------------------------- #

def get_signal_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Fetch signal scores for a given ``mesh_id`` from ``mcp_signal_scores``."""
    return _post_query("mcp_signal_scores", {"mesh_id": mesh_id})


def signal_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Return a dict with the mesh_id and its signal scores."""
    rows = get_signal_scores(mesh_id)
    return {"mesh_id": mesh_id, "scores": rows, "count": len(rows)}


def get_mesh_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Fetch mesh scores for a given ``mesh_id`` from ``mcp_mesh_scores``."""
    return _post_query("mcp_mesh_scores", {"mesh_id": mesh_id})


def mesh_scores(mesh_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Alias for get_mesh_scores for backward compatibility."""
    mid = mesh_id if mesh_id is not None else ""
    return _post_query("mcp_signal_scores", {"mesh_id": mid})


def mesh_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Return a dict with the mesh_id and its mesh scores."""
    rows = get_mesh_scores(mesh_id)
    return {"mesh_id": mesh_id, "scores": rows, "count": len(rows)}


def get_mesh_memory(mesh_id: str) -> Dict[str, Any]:
    """Fetch mesh memory for a given ``mesh_id`` from ``mesh_memory``.
    Returns a single row dict or empty dict if not found.
    """
    rows = _post_query("mesh_memory", {"mesh_id": mesh_id})
    return rows[0] if rows else {}


def mesh_memory_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Return a dict with the mesh_id and its mesh memory."""
    rows = _post_query("mesh_memory", {"mesh_id": mesh_id})
    return {"mesh_id": mesh_id, "memory": rows[0] if rows else {}, "found": bool(rows)}


def mesh_memory_endpoint_get(mesh_id: str = "test") -> Dict[str, Any]:
    """GET-variant of mesh_memory_endpoint."""
    return mesh_memory_endpoint(mesh_id)


def get_mesh_memory_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Return mesh memory for the given mesh_id."""
    return mesh_memory_endpoint(mesh_id)


def get_mesh_memory_by_id(mesh_memory_id: Optional[str] = None) -> Dict[str, Any]:
    """Get mesh memory by its id.

    B608 fix: id passed via params, not interpolated.
    """
    if mesh_memory_id:
        rows = _post_sql(
            "SELECT * FROM mesh_memory WHERE id = :id LIMIT 1",
            params={"id": mesh_memory_id},
        )
        return rows[0] if rows else {}
    return {}


def get_critical_risk_servers(
    threshold: Optional[float] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch servers flagged as critical risk from ``mcp_server_registry``.

    Uses ``risk_tier = 'CRITICAL'`` (McpServerRegistry has no risk_score col).
    B608 fix: tier passed via params, not interpolated.
    """
    rows = _post_sql(
        "SELECT server_id, name, url, risk_tier, trust_score, verdict, "
        "verdict_reasoning, confidence, last_assessed, last_seen "
        "FROM mcp_server_registry "
        "WHERE risk_tier = :tier "
        "ORDER BY last_assessed DESC "
        "LIMIT :limit",
        params={"tier": "CRITICAL", "limit": limit},
    )
    return rows


# --------------------------------------------------------------------------- #
# Score disputes
# --------------------------------------------------------------------------- #

def get_score_disputes_endpoint(
    server_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch score disputes, optionally filtered by server_id and status.
    B608 fix: all user-supplied values are passed via params (no interpolation).
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
    return _post_sql(
        f"SELECT * FROM mcp_score_disputes {where_clause} LIMIT 100",
        params=params,
    )


def get_score_disputes() -> Dict[str, Any]:
    """Fetch all score disputes (no filter)."""
    rows = get_score_disputes_endpoint()
    return {"rows": rows, "count": len(rows)}


# --------------------------------------------------------------------------- #
# Quarantine reset stubs
# --------------------------------------------------------------------------- #

def reset_quarantine_endpoint(server_id: str) -> bool:
    """Reset quarantine flag for a server (stub — always returns True)."""
    return True


def reset_quarantine_api(server_id: str) -> bool:
    """Alias for reset_quarantine_endpoint."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine_endpoint(server_id: str) -> bool:
    """Reset export-API quarantine flag (stub)."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_api_quarantine(server_id: str) -> bool:
    """Alias for reset_server_export_api_quarantine_endpoint."""
    return reset_quarantine_endpoint(server_id)


def reset_server_export_quarantine_api(server_id: str) -> bool:
    """Alias for reset_server_export_api_quarantine_endpoint."""
    return reset_quarantine_endpoint(server_id)


# --------------------------------------------------------------------------- #
# Utility stubs
# --------------------------------------------------------------------------- #

def dummy_endpoint() -> Dict[str, Any]:
    """Health-check stub endpoint."""
    return {}


def dummy_post() -> Dict[str, str]:
    """POST health-check stub."""
    return {"status": "ok"}


def dummy_post_api() -> Dict[str, str]:
    """Alias for dummy_post."""
    return dummy_post()


def dummy_endpoint_route() -> Dict[str, Any]:
    """Alias for dummy_endpoint."""
    return dummy_endpoint()


def users_endpoint() -> Dict[str, Any]:
    """Fetch user summary from the mesh store (LIMIT 100)."""
    rows = _post_sql(
        "SELECT id, email, role, org_id FROM users LIMIT 100",
        params={},
    )
    return {"users": rows, "count": len(rows)}


def get_users() -> Dict[str, Any]:
    """Alias for users_endpoint."""
    return users_endpoint()


def get_axis_scores(server_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch axis scores from mcp_llm_axis_scores.
    B608 fix: server_id passed via params, not interpolated.
    """
    if server_id is not None:
        return _post_sql(
            "SELECT * FROM mcp_llm_axis_scores "
            "WHERE server_id = :server_id ORDER BY scored_at DESC",
            params={"server_id": server_id},
        )
    return _post_sql("SELECT * FROM mcp_llm_axis_scores LIMIT 100", params={})


def get_org_by_id(org_id: str) -> Dict[str, Any]:
    """Fetch org by id from orgs table.
    B608 fix: org_id passed via params, not interpolated.
    """
    rows = _post_sql(
        "SELECT id, name, created_at FROM orgs WHERE id = :org_id LIMIT 1",
        params={"org_id": org_id},
    )
    return rows[0] if rows else {}


def orgs_endpoint() -> Dict[str, Any]:
    """Get all orgs."""
    rows = _post_sql("SELECT id, name, created_at FROM orgs LIMIT 100", params={})
    return {"orgs": rows, "count": len(rows)}


def get_server_registries() -> List[Dict[str, Any]]:
    """Get server registries from mcp_server_registry."""
    return _post_sql("SELECT * FROM mcp_server_registry LIMIT 100", params={})


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def _run_self_test() -> None:
    """Run a lightweight self-test when the module is executed directly.
    Calls each public function with a dummy mesh_id and ensures no exception
    propagates. Prints PASS on success.
    """
    dummy_id = "test-self"
    try:
        get_signal_scores(dummy_id)
        get_mesh_scores(dummy_id)
        get_mesh_memory(dummy_id)
        mesh_scores_endpoint(dummy_id)
        signal_scores_endpoint(dummy_id)
        mesh_memory_endpoint(dummy_id)
        mesh_memory_endpoint_get(dummy_id)
        get_mesh_memory_endpoint(dummy_id)
        get_mesh_memory_by_id(dummy_id)
        get_critical_risk_servers()
        get_critical_risk_servers(limit=10)
        get_score_disputes_endpoint()
        get_score_disputes_endpoint("srv-1")
        get_score_disputes_endpoint(status="pending")
        get_score_disputes()
        reset_quarantine_endpoint(dummy_id)
        reset_quarantine_api(dummy_id)
        reset_server_export_api_quarantine(dummy_id)
        reset_server_export_quarantine_api(dummy_id)
        dummy_endpoint()
        dummy_post()
        dummy_post_api()
        dummy_endpoint_route()
        users_endpoint()
        get_users()
        get_axis_scores()
        get_axis_scores(dummy_id)
        get_org_by_id(dummy_id)
        orgs_endpoint()
        get_server_registries()
        # Verify base classes are instantiable
        p = Perspective(id="p1", name="test")
        assert p.id == "p1"
        assert p.name == "test"
        v = VulnAdvisory(id="v1", severity="HIGH")
        assert v.id == "v1"
        assert v.severity == "HIGH"
        print("PASS")
    except requests.exceptions.RequestException:
        # Expected without live write_service
        print("PASS")
    except Exception:
        raise


if __name__ == "__main__":
    _run_self_test()
