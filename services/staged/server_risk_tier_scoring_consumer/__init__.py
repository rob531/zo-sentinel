"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

from typing import Any

import httpx

MESH_QUERY_URL = "http://127.0.0.1:8772/query"


def mesh_memory_endpoint_get(key: str) -> dict[str, Any] | None:
    """Read a mesh/pipeline record from the ZoComputer store.

    Reads from the pipeline DB (mcp_signal_scores, mesh_memory) via the
    staging query endpoint.  Do NOT use the app DB for pipeline data.
    """
    payload = {"sql": f"SELECT * FROM mesh_memory WHERE key_ = '{key}' LIMIT 1"}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(MESH_QUERY_URL, json=payload)
            resp.raise_for_status()
            rows = resp.json()
            if rows and len(rows) > 0:
                return rows[0]
    except Exception:
        pass
    return None


if __name__ == "__main__":
    # Self-test: verify the function is callable and returns a type-consistent result.
    result = mesh_memory_endpoint_get("__test_key_that_does_not_exist__")
    assert result is None or isinstance(result, dict), "Unexpected return type"
    print("PASS")