"""Shared utilities for staged services.

Provides:
- `get_db`: FastAPI dependency that yields an app DB session.
- `query_bus`: Helper to POST queries to the ZoComputer write‑service bus.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends

# App DB session import – must not be re‑implemented.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)  # noqa: F401  (re‑exported for callers that need model classes)

# --------------------------------------------------------------------------- #
# FastAPI dependency that returns the app DB session.
# --------------------------------------------------------------------------- #
def get_db(session: Any = Depends(get_session)) -> Any:
    """FastAPI dependency that provides a DB session."""
    return session


# --------------------------------------------------------------------------- #
# Write‑service bus tables (authoritative list from `schema/bus_catalog.json`).
# --------------------------------------------------------------------------- #
_BUS_TABLES = {
    "agent_outputs",
    "agent_runs",
    "audit_log",
    "auth_tokens",
    "build_churn_daily",
    "build_churn_trend",
    "build_provenance",
    "bulk_assess_jobs",
    "bulk_imports",
    "code_edges",
    "code_nodes",
    "corrections",
    "e2e_axis_scores",
    "e2e_servers",
    "failure_matrix",
    "forensic_cache",
    "github_velocity",
    "inference_log",
    "key_chain_status",
    "key_topology",
    "mcp_attestations",
    "mcp_decisions",
    "mcp_definition_history",
    "mcp_ecosystems_metadata",
    "mcp_exemptions",
    "mcp_fingerprints",
    "mcp_llm_axis_scores",
    "mcp_policy_rules",
    "mcp_risk_register",
    "mcp_risk_timeline",
    "mcp_server_registry",
    "mcp_signal_enrichments",
    "mcp_signal_scores",
    "mcp_submissions",
    "mcp_threat_associations",
    "mcp_tool_hashes",
    "mesh_events",
    "mesh_memory",
    "npm_typosquat_alerts",
    "perf_metrics",
    "service_health",
    "shodan_results",
    "threat_intel_articles",
    "world_articles",
    "world_topics",
    "write_queue_log",
}


def query_bus(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """POST a query to the write‑service bus.

    Args:
        table: Name of the bus table to query. Must be in the allowed list.
        filters: Optional dict of filter criteria.

    Returns:
        List of row dictionaries returned by the service.

    Raises:
        ValueError: If `table` is not a known bus table.
        RuntimeError: If the HTTP request fails or returns a non‑200 status.
    """
    if table not in _BUS_TABLES:
        raise ValueError(f"Unknown bus table: {table!r}")

    payload: Dict[str, Any] = {"table": table, "filters": filters or {}}
    resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=5)

    if resp.status_code != 200:
        raise RuntimeError(f"Bus query failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    # The write‑service returns rows under a top‑level key; adapt if needed.
    return data.get("rows", [])


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # Minimal FastAPI app to demonstrate dependency override.
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: "dummy_session"

    # ---- table validation test ------------------------------------------------
    try:
        query_bus("nonexistent_table")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid table name was not rejected")

    # ---- successful query test (mocked requests) -----------------------------
    class _DummyResponse:
        status_code = 200

        @staticmethod
        def json() -> Dict[str, Any]:
            return {"rows": []}

    def _dummy_post(url: str, json: Dict[str, Any], timeout: int = 5) -> _DummyResponse:  # noqa: D401
        """Mock `requests.post` used in the self‑test."""
        assert url == "http://127.0.0.1:8772/query"
        assert json["table"] == "mesh_memory"
        return _DummyResponse()

    _real_post = requests.post
    requests.post = _dummy_post
    try:
        rows = query_bus("mesh_memory")
        assert isinstance(rows, list)
    finally:
        requests.post = _real_post

    print("PASS")


__all__ = ["get_db", "query_bus"]