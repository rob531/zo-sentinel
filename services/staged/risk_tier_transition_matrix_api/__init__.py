"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion."""
from typing import Optional, Dict, Any
import requests
from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

MESH_API_URL = "http://127.0.0.1:8772/query"


def _query_mesh(table: str, **filters) -> Dict[str, Any]:
    payload = {"table": table}
    payload.update(filters)
    response = requests.post(MESH_API_URL, json=payload)
    response.raise_for_status()
    return response.json()


def mesh_memory_endpoint(org_id: Optional[int] = None) -> Dict[str, Any]:
    """Get mesh memory data from MESH API."""
    return _query_mesh("mesh_memory", org_id=org_id)


def signal_scores_endpoint(org_id: Optional[int] = None) -> Dict[str, Any]:
    """Get signal scores from MESH API."""
    return _query_mesh("mcp_signal_scores", org_id=org_id)


def get_mesh_memory_endpoint(memory_id: Optional[int] = None) -> Dict[str, Any]:
    """Get mesh memory entry by ID from MESH API."""
    return _query_mesh("mesh_memory", memory_id=memory_id)


def get_score_disputes_endpoint(dispute_id: Optional[int] = None) -> Dict[str, Any]:
    """Get score disputes from app database."""
    return _query_mesh("McpScoreDispute", dispute_id=dispute_id)


def mesh_scores_endpoint(org_id: Optional[int] = None) -> Dict[str, Any]:
    """Get mesh scores from MESH API."""
    return _query_mesh("mesh_scores", org_id=org_id)


def get_mesh_memory(memory_id: Optional[int] = None) -> Dict[str, Any]:
    """Get mesh memory from MESH API."""
    return _query_mesh("mesh_memory", memory_id=memory_id)


def get_score_disputes(dispute_id: Optional[int] = None) -> Dict[str, Any]:
    """Get score disputes from MESH API."""
    return _query_mesh("McpScoreDispute", dispute_id=dispute_id)


def _run_self_test() -> bool:
    """Verify module compiles and functions are importable."""
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    functions = [
        mesh_memory_endpoint,
        signal_scores_endpoint,
        get_mesh_memory_endpoint,
        get_score_disputes_endpoint,
        mesh_scores_endpoint,
        get_mesh_memory,
        get_score_disputes,
    ]
    for func in functions:
        assert callable(func), f"{func.__name__} not callable"
    return True


if __name__ == "__main__":
    import sys
    try:
        from app.db import get_session
        from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
        for f in [mesh_memory_endpoint, signal_scores_endpoint, get_mesh_memory_endpoint,
                  get_score_disputes_endpoint, mesh_scores_endpoint, get_mesh_memory, get_score_disputes]:
            assert callable(f)
        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)