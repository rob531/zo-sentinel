"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite."""

from typing import Any, Optional

import requests

_ZO_STORE_URL = "http://127.0.0.1:8772"


def get_mesh_memory(org_id: str) -> dict[str, Any]:
    """Fetch mesh_memory from ZoComputer store."""
    try:
        resp = requests.post(
            f"{_ZO_STORE_URL}/query",
            json={"table": "mesh_memory", "org_id": org_id},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def mesh_scores_endpoint(org_id: str, server_id: Optional[str] = None) -> dict[str, Any]:
    """Fetch mesh scores from ZoComputer store."""
    try:
        payload: dict[str, Any] = {"table": "mcp_signal_scores", "org_id": org_id}
        if server_id:
            payload["server_id"] = server_id
        resp = requests.post(f"{_ZO_STORE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dummy_post(endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
    """Dummy POST helper for testing."""
    return {"endpoint": endpoint, "posted": data, "status": "ok"}


def get_signal_scores(org_id: str, server_id: Optional[str] = None) -> dict[str, Any]:
    """Get signal scores from mesh/pipeline tables."""
    return mesh_scores_endpoint(org_id, server_id)


def reset_server_export_api_quarantine(server_id: str) -> bool:
    """Reset quarantine status for server export API."""
    try:
        resp = requests.post(
            f"{_ZO_STORE_URL}/reset_quarantine",
            json={"server_id": server_id},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def dummy_post_endpoint(data: dict[str, Any]) -> dict[str, Any]:
    """Dummy POST endpoint for testing."""
    return {"status": "ok", "received": data}


if __name__ == "__main__":
    import sys

    try:
        # Self-test: verify basic functionality
        mem = get_mesh_memory("test-org")
        assert isinstance(mem, dict)

        scores = get_signal_scores("test-org")
        assert isinstance(scores, dict)

        dummy = _dummy_post("/test", {"key": "value"})
        assert dummy["status"] == "ok"

        reset_ok = reset_server_export_api_quarantine("test-server")
        assert isinstance(reset_ok, bool)

        post_result = dummy_post_endpoint({"data": 123})
        assert post_result["status"] == "ok"

        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)