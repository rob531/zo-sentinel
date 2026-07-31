
"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion without rewrite."""

from typing import Any, Dict, List
import httpx
from app.db import get_session
from app.models import MCPServerRegistry

# Security: Set default timeout for all requests
DEFAULT_TIMEOUT = 30.0

def _dummy_post(
    url: str,
    data: Dict[str, Any],
    headers: Dict[str, str],
    params: Dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT
) -> Dict[str, Any]:
    """Dummy POST endpoint for testing."""
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=data, headers=headers, params=params)
        return response.json()

def _mesh_query(
    query: str,
    params: Dict[str, Any],
    headers: Dict[str, str],
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 3
) -> List[Dict[str, Any]]:
    """Query mesh service with retry logic."""
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    "http://mesh-service/query",
                    json={"query": query, "params": params},
                    headers=headers
                )
                return response.json()
        except (httpx.RequestError, httpx.TimeoutException) as e:
            if attempt == retries - 1:
                raise
    return []

def get_signal_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Get signal scores for a mesh ID."""
    session = get_session()
    server = session.query(MCPServerRegistry).filter_by(mesh_id=mesh_id).first()
    if not server:
        return []

    return _mesh_query(
        "GET_SIGNAL_SCORES",
        {"mesh_id": mesh_id},
        {"Authorization": f"Bearer {server.api_key}"}
    )

def signal_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Endpoint for getting signal scores."""
    return {
        "mesh_id": mesh_id,
        "scores": get_signal_scores(mesh_id)
    }

def get_mesh_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Get mesh scores for a mesh ID."""
    session = get_session()
    server = session.query(MCPServerRegistry).filter_by(mesh_id=mesh_id).first()
    if not server:
        return []

    return _mesh_query(
        "GET_MESH_SCORES",
        {"mesh_id": mesh_id},
        {"Authorization": f"Bearer {server.api_key}"}
    )

def mesh_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Endpoint for getting mesh scores."""
    return {
        "mesh_id": mesh_id,
        "scores": get_mesh_scores(mesh_id)
    }

def get_mesh_memory(mesh_id: str) -> Dict[str, Any]:
    """Get mesh memory for a mesh ID."""
    session = get_session()
    server = session.query(MCPServerRegistry).filter_by(mesh_id=mesh_id).first()
    if not server:
        return {}

    response = _mesh_query(
        "GET_MESH_MEMORY",
        {"mesh_id": mesh_id},
        {"Authorization": f"Bearer {server.api_key}"}
    )
    return response[0] if response else {}

def mesh_memory_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Endpoint for getting mesh memory."""
    return {
        "mesh_id": mesh_id,
        "memory": get_mesh_memory(mesh_id)
    }

def _run_self_test() -> Dict[str, Any]:
    """Run self-test for the service."""
    # Test dummy post
    dummy_result = _dummy_post(
        "http://example.com/test",
        {"key": "value"},
        {"Content-Type": "application/json"},
        {"param": "value"}
    )

    # Test signal scores
    signal_result = signal_scores_endpoint()

    # Test mesh scores
    mesh_result = mesh_scores_endpoint()

    # Test mesh memory
    memory_result = mesh_memory_endpoint()

    return {
        "status": "success",
        "results": {
            "dummy_post": dummy_result,
            "signal_scores": signal_result,
            "mesh_scores": mesh_result,
            "mesh_memory": memory_result
        }
    }

if __name__ == "__main__":
    # Run self-test when executed directly
    test_result = _run_self_test()
    print("Self-test result:", test_result)
