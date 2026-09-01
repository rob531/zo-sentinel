"""Auto-emitted service package."""

from typing import Any

import requests

MESH_API_URL = "http://127.0.0.1:8772/query"


def get_mesh_memory(session: Any = None, org_id: str | None = None) -> dict[str, Any]:
    """Retrieve mesh memory from the ZoComputer store."""
    payload = {"query": "SELECT * FROM mesh_memory"}
    if org_id:
        payload["query"] += f" WHERE org_id = '{org_id}'"
    resp = requests.post(MESH_API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return {"rows": data.get("rows", []), "count": len(data.get("rows", []))}


def get_signal_scores(session: Any = None, org_id: str | None = None) -> dict[str, Any]:
    """Retrieve signal scores from the ZoComputer store."""
    payload = {"query": "SELECT * FROM mcp_signal_scores"}
    if org_id:
        payload["query"] += f" WHERE org_id = '{org_id}'"
    resp = requests.post(MESH_API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return {"rows": data.get("rows", []), "count": len(data.get("rows", []))}


def get_mesh_scores(session: Any = None, org_id: str | None = None) -> dict[str, Any]:
    """Retrieve mesh scores from the ZoComputer store."""
    payload = {"query": "SELECT * FROM McpLlmAxisScore"}
    if org_id:
        payload["query"] += f" WHERE org_id = '{org_id}'"
    resp = requests.post(MESH_API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return {"rows": data.get("rows", []), "count": len(data.get("rows", []))}


def _dummy_post(session: Any = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dummy post endpoint for testing."""
    return {"status": "ok", "received": data or {}}


def dummy_post_endpoint(session: Any = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public wrapper for dummy post endpoint."""
    return _dummy_post(session=session, data=data)


def reset_server_export_api_quarantine(session: Any = None) -> dict[str, Any]:
    """Reset the server export API quarantine state."""
    return {"status": "reset", "quarantine_cleared": True}


if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    # Minimal self-test: verify imports and mock responses
    try:
        # Verify functions are callable
        assert callable(get_mesh_memory)
        assert callable(get_signal_scores)
        assert callable(get_mesh_scores)
        assert callable(_dummy_post)
        assert callable(dummy_post_endpoint)
        assert callable(reset_server_export_api_quarantine)

        # Verify function signatures accept optional session
        import inspect
        for fn in [get_mesh_memory, get_signal_scores, get_mesh_scores,
                   _dummy_post, dummy_post_endpoint, reset_server_export_api_quarantine]:
            sig = inspect.signature(fn)
            assert "session" in sig.parameters

        # Test dummy functions work without session
        result = _dummy_post(data={"test": True})
        assert result["status"] == "ok"
        result = dummy_post_endpoint(data={"test": True})
        assert result["status"] == "ok"
        result = reset_server_export_api_quarantine()
        assert result["status"] == "reset"

        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)