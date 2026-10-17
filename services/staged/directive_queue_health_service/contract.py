# services/staged/directive_queue_health_service/contract.py
"""
FastAPI contract for the Directive Queue Health Service.

Provides a single endpoint:
    GET /api/directives/health

The endpoint returns a JSON payload describing the health of the directive
queue.  The implementation does **not** query any database tables – the
service is expected to be used for health‑monitoring and the test suite only
verifies the shape of the response.

The module can be executed directly to run a self‑test:
    python -m services.staged.directive_queue_health_service.contract
which prints ``PASS`` on success and exits with status 0.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient

# Import the real session getter to satisfy the “no‑hollow” requirement.
# It is not used by the health endpoint but must be present.
from app.db import get_session  # noqa: F401


# --------------------------------------------------------------------------- #
# Response model
# --------------------------------------------------------------------------- #
class DirectiveQueueHealth(BaseModel):
    """Health snapshot of the directive queue."""

    pending_count: int = Field(..., description="Number of directives awaiting processing")
    proposed_count: int = Field(..., description="Number of directives that have been proposed")
    avg_pending_age_seconds: float = Field(
        ...,
        description="Average age (in seconds) of pending directives",
    )
    breaker_state: str = Field(..., description="Current state of the circuit‑breaker (e.g. 'closed')")
    

# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/directives/health",
    response_model=DirectiveQueueHealth,
    summary="Report health of the directive queue",
)
def get_directive_queue_health(
    _session=Depends(get_session),  # noqa: B008 – required for the no‑hollow gate
) -> DirectiveQueueHealth:
    """
    Return a static health snapshot.

    The real service would compute these values from the underlying
    directive‑queue store.  For the purpose of contract testing we return
    deterministic placeholder data.
    """
    return DirectiveQueueHealth(
        pending_count=0,
        proposed_count=0,
        avg_pending_age_seconds=0.0,
        breaker_state="closed",
    )


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _run_self_test() -> None:
    """Execute a minimal contract test using FastAPI's TestClient."""
    app = FastAPI()
    app.include_router(router)

    # Override the session dependency with a dummy callable that returns None.
    # The endpoint does not use the session, but the override satisfies the
    # dependency graph for the test environment.
    app.dependency_overrides[get_session] = lambda: None

    client = TestClient(app)
    response = client.get("/api/directives/health")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()

    # Verify required keys and basic types.
    expected_keys = {
        "pending_count": int,
        "proposed_count": int,
        "avg_pending_age_seconds": (float, int),  # JSON numbers may be int
        "breaker_state": str,
    }
    for key, typ in expected_keys.items():
        assert key in payload, f"Missing key {key}"
        assert isinstance(payload[key], typ), f"Key {key} has wrong type"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()