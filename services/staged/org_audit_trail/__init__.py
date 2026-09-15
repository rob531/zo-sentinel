"""
zo-sentinel service package core utilities.

Provides a minimal FastAPI router and health endpoint that can be
imported by staged service modules without modification.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

# Application data layer imports – must remain exactly as specified.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

__all__ = [
    "router",
    "health_check",
    "include_router",
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
]

router = APIRouter()


@router.get("/health", tags=["service"])
def health_check() -> dict[str, str]:
    """Simple health endpoint used by many staged services."""
    return {"status": "ok"}


def include_router(app: FastAPI) -> None:
    """
    Attach the package router to a FastAPI application.

    Staged modules can call this to expose the health endpoint without
    needing to know the internal router name.
    """
    app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build a minimal FastAPI app for the self‑test.
    test_app = FastAPI()
    include_router(test_app)

    # Override the DB session dependency with a no‑op stub.
    def _dummy_session():
        return None

    test_app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(test_app)

    resp = client.get("/health")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
    assert resp.json() == {"status": "ok"}, f"Unexpected body: {resp.json()}"

    print("PASS")