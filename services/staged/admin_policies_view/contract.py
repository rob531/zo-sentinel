"""services/staged/admin_policies_view/contract.py

FastAPI router exposing the admin policies view.
"""

from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class Policy(BaseModel):
    """Policy representation returned by the endpoint."""
    policy_id: int
    name: str
    description: Optional[str] = None


# ----------------------------------------------------------------------
# Data‑access helpers
# ----------------------------------------------------------------------
def _fetch_policies_from_write_service() -> List[dict]:
    """
    Query the write‑service (http://127.0.0.1:8772/query) for policy rows.

    The write‑service expects a JSON payload with a ``sql`` key.
    Returns a list of row dictionaries.
    """
    url = "http://127.0.0.1:8772/query"
    sql = "SELECT policy_id, name, description FROM mcp_policy_rules"
    try:
        resp = requests.post(url, json={"sql": sql}, timeout=5.0)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc))
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="write‑service error")
    payload = resp.json()
    # The write‑service convention: rows are under the ``rows`` key.
    return payload.get("rows", [])


def get_policies_dep() -> List[dict]:
    """FastAPI dependency that supplies the raw policy rows."""
    return _fetch_policies_from_write_service()


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/admin/policies", response_model=List[Policy])
def admin_policies(policies: List[dict] = Depends(get_policies_dep)):
    """Return the list of admin policies."""
    return [Policy(**row) for row in policies]


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # The self‑test builds a minimal FastAPI app, injects a mock
    # implementation of ``get_policies_dep`` and verifies the endpoint.
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Mock data returned by the write‑service
    def _mock_get_policies_dep() -> List[dict]:
        return [
            {"policy_id": 1, "name": "Policy One", "description": "First policy"},
            {"policy_id": 2, "name": "Policy Two", "description": "Second policy"},
        ]

    app.dependency_overrides[get_policies_dep] = _mock_get_policies_dep

    client = TestClient(app)
    response = client.get("/admin/policies")
    assert response.status_code == 200, f"unexpected status {response.status_code}"
    data = response.json()
    assert isinstance(data, list) and len(data) == 2, "unexpected payload"
    assert data[0]["policy_id"] == 1, "first policy id mismatch"
    assert data[1]["name"] == "Policy Two", "second policy name mismatch"
    print("PASS")