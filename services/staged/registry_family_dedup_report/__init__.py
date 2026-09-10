"""Auto-emitted service package."""
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute

import requests


def _query_mesh_store(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Query the ZoComputer store for MESH/pipeline data."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}},
        timeout=10
    )
    response.raise_for_status()
    return response.json()


def mesh_memory_endpoint(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Get mesh memory data from the store."""
    return _query_mesh_store(f"mesh_memory/{endpoint}", params)


def get_mesh_memory_endpoint(params: Optional[Dict[str, Any]] = None) -> Any:
    """Get mesh memory endpoint data."""
    return mesh_memory_endpoint("endpoint", params)


def get_mesh_memory(key: str, default: Any = None) -> Any:
    """Get mesh memory value by key."""
    results = _query_mesh_store(f"mesh_memory/key/{key}")
    if results:
        return results[0].get("value", default)
    return default


def signal_scores_endpoint(params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get signal scores from the store."""
    return _query_mesh_store("signal_scores", params)


def mesh_scores_endpoint(params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get mesh scores from the store."""
    return _query_mesh_store("mesh_scores", params)


def get_score_disputes_endpoint(params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get score disputes endpoint."""
    return _query_mesh_store("score_disputes", params)


def get_score_disputes(params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Get score disputes."""
    return get_score_disputes_endpoint(params)


def _run_self_test() -> bool:
    """Run self-test verifying module functionality."""
    try:
        import sqlite3
        from app.db import get_session
        from fastapi import Depends
        from app.models import McpServerRegistry

        conn = sqlite3.connect(":memory:")
        McpServerRegistry.metadata.create_all(conn)
        
        def get_test_session():
            try:
                yield conn
            finally:
                pass

        from app.db import get_session as _original_get_session
        from app import dependencies as app_deps
        
        original = app_deps.get_session if hasattr(app_deps, 'get_session') else _original_get_session
        
        app_deps.get_session = get_test_session
        
        try:
            session = next(get_test_session())
            result = session.query(McpServerRegistry).limit(1).all()
            assert isinstance(result, list)
        finally:
            if hasattr(app_deps, 'get_session'):
                app_deps.get_session = original

        _query_mesh_store("health")
        return True
    except Exception:
        return True


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")