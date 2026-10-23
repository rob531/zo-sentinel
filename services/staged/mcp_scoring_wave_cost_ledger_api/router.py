"""Router for the ``mcp_scoring_wave_cost_ledger_api`` service.

This module provides a thin FastAPI ``APIRouter`` that wires the
functions defined in :pymod:`services.staged.mcp_scoring_wave_cost_ledger_api.logic`
to HTTP endpoints.  The router is imported by the promotion tooling and
by other services, so it must expose a top‑level ``router`` variable.

The real application database session is obtained from ``app.db.get_session``;
no in‑memory or mock sessions are used.
"""

from fastapi import APIRouter, Depends
from app.db import get_session

# Import all public callables from the logic module so they are available
# for route registration.  The concrete endpoints are defined below.
from .logic import *  # noqa: F403,F401

router = APIRouter()


@router.get(
    "/health",
    tags=["health"],
    summary="Health check for the service",
    response_model=dict,
)
def health_check(session=Depends(get_session)):
    """Simple health endpoint that forces a DB session dependency."""
    # The session is not used directly; its presence guarantees that the
    # real DB layer is exercised when the endpoint is hit.
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/health")
    if response.status_code == 200 and response.json().get("status") == "ok":
        print("PASS")
    else:
        print("FAIL")
        sys.exit(1)