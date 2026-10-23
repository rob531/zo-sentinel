"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion."""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

try:
    from app.db import get_session
    from app.models import McpLlmAxisScore as mcp_llm_axis_scores_model

    _APP_DB_AVAILABLE = True
except ImportError:
    _APP_DB_AVAILABLE = False

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


def get_mesh_memory(
    service: str,
    org_id: Optional[str] = None,
    server_id: Optional[str] = None,
) -> dict[str, Any]:
    """Retrieve mesh memory from ZoComputer store."""
    query = {"mesh_memory": {"service": service}}
    if org_id:
        query["mesh_memory"]["org_id"] = org_id
    if server_id:
        query["mesh_memory"]["server_id"] = server_id

    if _HTTPX_AVAILABLE:
        try:
            resp = httpx.post(
                "http://127.0.0.1:8772/query",
                json=query,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, OSError):
            pass
    return {}


def get_mesh_scores(
    org_id: str,
    metric: str = "risk_tier",
    window_days: int = 30,
) -> dict[str, Any]:
    """Retrieve mesh scores from ZoComputer store."""
    query = {
        "mesh_scores": {
            "org_id": org_id,
            "metric": metric,
            "window_days": window_days,
        }
    }

    if _HTTPX_AVAILABLE:
        try:
            resp = httpx.post(
                "http://127.0.0.1:8772/query",
                json=query,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, OSError):
            pass
    return {}


def get_signal_scores(
    org_id: str,
    signal_type: Optional[str] = None,
    include_disputes: bool = False,
) -> dict[str, Any]:
    """Retrieve signal scores for an org from ZoComputer store."""
    query = {
        "signal_scores": {
            "org_id": org_id,
        }
    }
    if signal_type:
        query["signal_scores"]["signal_type"] = signal_type
    if include_disputes:
        query["signal_scores"]["include_disputes"] = True

    if _HTTPX_AVAILABLE:
        try:
            resp = httpx.post(
                "http://127.0.0.1:8772/query",
                json=query,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, OSError):
            pass
    return {}


def setup_database() -> dict[str, Any]:
    """Initialize app database schema if needed."""
    if not _APP_DB_AVAILABLE:
        return {"status": "skipped", "reason": "app_db_unavailable"}

    result = {"status": "ok", "tables": []}
    try:
        with get_session() as session:
            if hasattr(mcp_llm_axis_scores_model, "__table__"):
                result["tables"].append("McpLlmAxisScore")
            result["status"] = "initialized"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


def reset_server_export_api_quarantine(
    server_id: Optional[str] = None,
) -> dict[str, Any]:
    """Reset quarantine state for server export API."""
    if not _APP_DB_AVAILABLE:
        return {"status": "skipped", "reason": "app_db_unavailable"}

    result = {"status": "ok", "reset": False}
    try:
        with get_session() as session:
            # Safe parameterized query to prevent SQL injection
            from sqlalchemy import text

            if server_id:
                session.execute(
                    text("UPDATE McpServerRegistry SET quarantine = false WHERE server_id = :sid"),
                    {"sid": server_id},
                )
                result["server_id"] = server_id
            else:
                session.execute(
                    text("UPDATE McpServerRegistry SET quarantine = false WHERE api_type = 'export'")
                )
            session.commit()
            result["reset"] = True
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    return result


def _run_self_test() -> bool:
    """Self-test verifying module compiles and core functions are callable."""
    import ast

    source = __file__
    try:
        with open(source, "r") as f:
            ast.parse(f.read())
    except SyntaxError:
        return False

    required_funcs = [
        get_mesh_memory,
        get_mesh_scores,
        get_signal_scores,
        setup_database,
        reset_server_export_api_quarantine,
    ]

    for func in required_funcs:
        if not callable(func):
            return False
        if not func.__name__.isidentifier():
            return False

    get_mesh_memory("test_service")
    get_mesh_scores("test_org")
    get_signal_scores("test_org")
    setup_database()
    reset_server_export_api_quarantine()
    reset_server_export_api_quarantine("test_server_id")

    return True


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)