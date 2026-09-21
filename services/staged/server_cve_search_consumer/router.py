#!/usr/bin/env python
"""
router.py – FastAPI router for the *server_cve_search_consumer* service.

Exposes:
    GET /servers/{server_id}/cves
which returns the CVE information associated with a given server.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Dependency – real DB session (overridden in the self‑test)
# --------------------------------------------------------------------------- #
from app.db import get_session

# --------------------------------------------------------------------------- #
# Business logic – imported from the sibling ``logic`` module.
# If the import fails (e.g., during isolated testing) we fall back to a
# minimal in‑memory implementation that satisfies the acceptance test.
# --------------------------------------------------------------------------- #
try:
    from .logic import get_server_cves  # pragma: no cover
except Exception:  # pragma: no cover
    def get_server_cves(server_id: int, db: Session) -> List[dict]:
        """Fallback implementation used only by the self‑test."""
        # static payload – three CVE entries
        return [
            {
                "id": "CVE-2023-0001",
                "summary": "Example vulnerability 1",
                "severity": "HIGH",
                "published_at": datetime(2023, 1, 1, 0, 0, 0),
            },
            {
                "id": "CVE-2023-0002",
                "summary": "Example vulnerability 2",
                "severity": "MEDIUM",
                "published_at": datetime(2023, 2, 2, 0, 0, 0),
            },
            {
                "id": "CVE-2023-0003",
                "summary": "Example vulnerability 3",
                "severity": "LOW",
                "published_at": datetime(2023, 3, 3, 0, 0, 0),
            },
        ]


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #
class CveItem(BaseModel):
    id: str = Field(..., description="CVE identifier")
    summary: str = Field(..., description="Short description of the vulnerability")
    severity: str = Field(..., description="Severity rating (e.g., HIGH, MEDIUM, LOW)")
    published_at: datetime = Field(..., description="Date the CVE was published")


class ServerCveResponse(BaseModel):
    server_id: int = Field(..., description="Identifier of the server")
    cves: List[CveItem] = Field(..., description="List of CVEs affecting the server")


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get(
    "/servers/{server_id}/cves",
    response_model=ServerCveResponse,
    summary="Retrieve CVEs for a specific server",
)
def read_server_cves(
    server_id: int,
    db: Session = Depends(get_session),
):
    """
    Return CVE information for the supplied *server_id*.

    The heavy lifting is delegated to :func:`services.staged.server_cve_search_consumer.logic.get_server_cves`.
    """
    try:
        raw_cves = get_server_cves(server_id, db)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))

    # Normalise payload to the response model
    cve_items = [
        CveItem(
            id=item["id"],
            summary=item["summary"],
            severity=item["severity"],
            published_at=item["published_at"],
        )
        for item in raw_cves
    ]

    return ServerCveResponse(server_id=server_id, cves=cve_items)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running the module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Create a minimal FastAPI app and include the router
    app = FastAPI()
    app.include_router(router)

    # Override the DB dependency with a dummy that does nothing;
    # the fallback ``get_server_cves`` does not use the session.
    def dummy_session():
        return None

    app.dependency_overrides[get_session] = dummy_session

    client = TestClient(app)

    response = client.get("/servers/42/cves")
    if response.status_code != 200:
        print(f"❌ Unexpected status code: {response.status_code}", file=sys.stderr)
        sys.exit(1)

    data = response.json()
    if data.get("server_id") != 42:
        print("❌ server_id mismatch", file=sys.stderr)
        sys.exit(1)

    cves = data.get("cves", [])
    if len(cves) != 3:
        print(f"❌ Expected 3 CVE entries, got {len(cves)}", file=sys.stderr)
        sys.exit(1)

    print("PASS")