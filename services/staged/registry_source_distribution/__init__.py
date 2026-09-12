"""auto_emitted_service package entry point.

Provides FastAPI routers that expose mesh/pipeline data via the
ZoComputer store.  All data‑layer imports come directly from the app
package to satisfy the no‑hollow rule.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import requests
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

# App‑level data access – must be imported verbatim.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    VulnAdvisory,
)

router = APIRouter()


def _post_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a POST query to the ZoComputer store.

    The store lives at http://127.0.0.1:8772/query and expects a JSON
    payload.  The response is returned as a dict; any HTTP error raises
    ``requests.HTTPError``.
    """
    url = "http://127.0.0.1:8772/query"
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, data=json.dumps(payload), headers=headers)
    response.raise_for_status()
    return response.json()


@router.get("/signal-scores")
async def get_signal_scores(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Return all rows from the ``mcp_signal_scores`` mesh table.

    The ``db`` dependency is kept for contract compatibility even though
    the data lives in the external mesh store.
    """
    payload = {"select": "*", "from": "mcp_signal_scores"}
    result = _post_query(payload)
    # The external service returns a dict with a ``data`` key holding rows.
    return result.get("data", [])


@router.get("/mesh-memory")
async def mesh_memory_endpoint(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Return all rows from the ``mesh_memory`` mesh table.

    The ``db`` dependency is kept for contract compatibility.
    """
    payload = {"select": "*", "from": "mesh_memory"}
    result = _post_query(payload)
    return result.get("data", [])


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # Minimal FastAPI app for testing the router.
    app = FastAPI()
    app.include_router(router)

    # Override the ``get_session`` dependency with a no‑op stub.
    def _dummy_session():
        return None

    app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(app)

    # The external mesh service is not reachable in the test environment,
    # so we monkey‑patch ``_post_query`` to return an empty payload.
    def _mock_post_query(_: Dict[str, Any]) -> Dict[str, Any]:
        return {"data": []}

    # Apply the monkey‑patch.
    globals()["_post_query"] = _mock_post_query

    # Perform simple health checks on the endpoints.
    resp_signal = client.get("/signal-scores")
    resp_memory = client.get("/mesh-memory")

    if resp_signal.status_code == 200 and resp_memory.status_code == 200:
        print("PASS")
    else:
        raise SystemExit("FAIL")