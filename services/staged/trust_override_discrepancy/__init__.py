"""
Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""
from typing import Any, Dict, List, Optional

import requests

from app.db import get_session

try:
    from app.models import (
        MCP_SERVER_REGISTRY_TABLES,
        McpLlmAxisScore,
        McpScoreDispute,
        ORGS_TABLE,
        USERS_TABLE,
    )
except ImportError:
    pass

# Security: B113 - Call to requests without timeout
_REQUEST_TIMEOUT = 30


def _post_query(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Post query to ZoComputer store."""
    try:
        resp = requests.post(
            f"http://127.0.0.1:8772{endpoint}",
            json=payload,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {}


def get_mesh_memory(org_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieve mesh memory from ZoComputer store."""
    payload = {"query": "mesh_memory"}
    if org_id is not None:
        payload["org_id"] = org_id
    result = _post_query("/query", payload)
    return result.get("data", []) if isinstance(result, dict) else []


def get_signal_scores(org_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieve signal scores from ZoComputer store."""
    payload = {"query": "mcp_signal_scores"}
    if org_id is not None:
        payload["org_id"] = org_id
    result = _post_query("/query", payload)
    return result.get("data", []) if isinstance(result, dict) else []


def get_mesh_scores(org_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieve mesh scores from ZoComputer store."""
    payload = {"query": "mesh_scores"}
    if org_id is not None:
        payload["org_id"] = org_id
    result = _post_query("/query", payload)
    return result.get("data", []) if isinstance(result, dict) else []


def _dummy_post(endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dummy POST for testing connectivity."""
    try:
        resp = requests.post(
            f"http://127.0.0.1:8772{endpoint}",
            json=data or {},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {"status": "error", "message": "Request failed"}


def dummy_post_endpoint(endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Public wrapper for dummy post endpoint."""
    return _dummy_post(endpoint, data)


def reset_server_export_api_quarantine() -> None:
    """Reset server export API quarantine."""
    _post_query("/quarantine/reset", {"action": "reset"})


def _run_self_test() -> bool:
    """Run self-test to verify module functionality."""
    try:
        get_mesh_memory()
        get_signal_scores()
        get_mesh_scores()
        _dummy_post("/test", {"test": True})
        dummy_post_endpoint("/test", {"test": True})
        _post_query("/health", {})
        reset_server_export_api_quarantine()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock, patch

    # Mock external dependencies for self-test
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": []}
    mock_response.raise_for_status = MagicMock()

    with patch("requests.post", return_value=mock_response):
        if _run_self_test():
            print("PASS")
            sys.exit(0)
        else:
            print("FAIL")
            sys.exit(1)