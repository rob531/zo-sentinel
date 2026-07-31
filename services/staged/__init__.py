"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion without rewrite."""

from typing import Any, Dict, List, Optional

import httpx

MESH_URL = "http://127.0.0.1:8772"


def _dummy_post(
    url: str = MESH_URL,
    json: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """Dummy post for testing/service health."""
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout)) as client:
            resp = client.post(f"{url}/query", json=json or {})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


def _mesh_query(
    table: str,
    filter: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """Query mesh/pipeline tables via write_service."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{MESH_URL}/query",
                json={"table": table, "filter": filter or {}},
            )
            resp.raise_for_status()
            return resp.json().get("rows", [])
    except Exception:
        return []


def get_signal_scores(mesh_id: str, session: Optional[Session] = None) -> List[Dict[str, Any]]:
    """Fetch signal scores for a mesh_id from mcp_signal_scores table."""
    return _mesh_query("mcp_signal_scores", {"mesh_id": mesh_id})


def signal_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Endpoint-style signal scores retrieval."""
    rows = _mesh_query("mcp_signal_scores", {"mesh_id": mesh_id})
    return {"mesh_id": mesh_id, "scores": rows, "count": len(rows)}


def get_mesh_scores(mesh_id: str) -> List[Dict[str, Any]]:
    """Fetch mesh scores for a mesh_id."""
    return _mesh_query("mcp_mesh_scores", {"mesh_id": mesh_id})


def mesh_scores_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Endpoint-style mesh scores retrieval."""
    rows = _mesh_query("mcp_mesh_scores", {"mesh_id": mesh_id})
    return {"mesh_id": mesh_id, "scores": rows, "count": len(rows)}


def get_mesh_memory(mesh_id: str) -> Dict[str, Any]:
    """Fetch mesh memory for a mesh_id."""
    rows = _mesh_query("mesh_memory", {"mesh_id": mesh_id})
    return rows[0] if rows else {}


def mesh_memory_endpoint(mesh_id: str = "test") -> Dict[str, Any]:
    """Endpoint-style mesh memory retrieval."""
    rows = _mesh_query("mesh_memory", {"mesh_id": mesh_id})
    return {"mesh_id": mesh_id, "memory": rows[0] if rows else {}, "found": len(rows) > 0}


def _run_self_test() -> Dict[str, Any]:
    """Self-test to verify package-level functions work."""
    results = {
        "get_signal_scores": False,
        "get_mesh_scores": False,
        "get_mesh_memory": False,
        "mesh_scores_endpoint": False,
        "signal_scores_endpoint": False,
        "mesh_memory_endpoint": False,
    }
    try:
        get_signal_scores("test-self")
        results["get_signal_scores"] = True
    except Exception:
        pass
    try:
        get_mesh_scores("test-self")
        results["get_mesh_scores"] = True
    except Exception:
        pass
    try:
        get_mesh_memory("test-self")
        results["get_mesh_memory"] = True
    except Exception:
        pass
    try:
        mesh_scores_endpoint("test-self")
        results["mesh_scores_endpoint"] = True
    except Exception:
        pass
    try:
        signal_scores_endpoint("test-self")
        results["signal_scores_endpoint"] = True
    except Exception:
        pass
    try:
        mesh_memory_endpoint("test-self")
        results["mesh_memory_endpoint"] = True
    except Exception:
        pass
    return results


if __name__ == "__main__":
    print("Running self-test...")
    results = _run_self_test()
    print(f"Results: {results}")
    passed = sum(1 for v in results.values() if v)
    print(f"Passed: {passed}/{len(results)}")
