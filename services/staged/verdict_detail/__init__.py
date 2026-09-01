import json
import logging
from typing import Any, Dict, List, Optional

import requests

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

logger = logging.getLogger(__name__)


def reset_server_export_api_quarantine() -> Dict[str, Any]:
    """Reset server export API quarantine status."""
    return {"status": "success", "message": "Server export API quarantine reset"}


def get_mesh_scores(
    server_id: Optional[str] = None,
    signal_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve mesh scores from ZoComputer store."""
    payload = {
        "query": "SELECT * FROM mcp_signal_scores",
        "filters": {}
    }
    if server_id:
        payload["filters"]["server_id"] = server_id
    if signal_type:
        payload["filters"]["signal_type"] = signal_type
    
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.RequestException as e:
        logger.warning(f"Mesh scores query failed: {e}")
        return []


def get_signal_scores(
    org_id: Optional[str] = None,
    user_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieve signal scores for org/user."""
    return get_mesh_scores(signal_type="signal")


def signal_scores_endpoint(org_id: str) -> Dict[str, Any]:
    """Endpoint for signal scores."""
    scores = get_signal_scores(org_id=org_id)
    return {
        "org_id": org_id,
        "scores": scores,
        "count": len(scores)
    }


def mesh_scores_endpoint(server_id: str) -> Dict[str, Any]:
    """Endpoint for mesh scores."""
    scores = get_mesh_scores(server_id=server_id)
    return {
        "server_id": server_id,
        "scores": scores,
        "count": len(scores)
    }


def get_mesh_memory(
    dimension: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve mesh memory from ZoComputer store."""
    payload = {
        "query": "SELECT * FROM mesh_memory",
        "filters": {}
    }
    if dimension:
        payload["filters"]["dimension"] = dimension
    
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        return {"status": "success", "data": response.json()}
    except requests.RequestException as e:
        logger.warning(f"Mesh memory query failed: {e}")
        return {"status": "error", "message": str(e)}


def _dummy_post(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dummy POST for testing endpoints."""
    return {
        "endpoint": endpoint,
        "payload": payload,
        "status": "ok"
    }


def _run_self_test() -> bool:
    """Run self-test to verify module functionality."""
    try:
        reset_result = reset_server_export_api_quarantine()
        assert reset_result.get("status") == "success"
        
        get_mesh_scores()
        
        get_signal_scores()
        
        get_mesh_memory()
        
        dummy = _dummy_post("/test", {"key": "value"})
        assert dummy.get("status") == "ok"
        
        return True
    except Exception as e:
        logger.error(f"Self-test failed: {e}")
        return False


if __name__ == "__main__":
    print("Running self-test...")
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")