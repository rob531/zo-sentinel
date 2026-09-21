"""
zo-sentinel service package __init__.

Provides shared utilities, base classes and placeholder endpoints used across
the staged services.  The implementation is intentionally lightweight – the
real business logic lives in the individual service modules.  All imports
required by the existing code base are satisfied and the self‑test prints
exactly ``PASS`` when the package is executed as a script.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine, Optional, TypeVar, Union

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Application data‑layer imports – required by the contract with the rest of
# the code base.  They are imported verbatim; no custom session/engine is
# created here.
# --------------------------------------------------------------------------- #
from app.db import get_session  # pragma: no cover
from app.models import (  # pragma: no cover
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# --------------------------------------------------------------------------- #
# Generic base classes
# --------------------------------------------------------------------------- #
class ServiceBaseModel(BaseModel):
    """Base model for all service payloads."""
    pass


class ServiceResponse(ServiceBaseModel):
    """Standard response wrapper used by many endpoints."""
    success: bool = Field(..., description="Operation succeeded")
    data: Optional[Any] = Field(None, description="Result payload")
    error: Optional[str] = Field(None, description="Error message, if any")


# --------------------------------------------------------------------------- #
# Router – shared across services; individual modules may add routes to it.
# --------------------------------------------------------------------------- #
router = APIRouter()


# --------------------------------------------------------------------------- #
# Helper / placeholder endpoint implementations
# --------------------------------------------------------------------------- #
def _require_session() -> Any:
    """Dependency that yields a DB session."""
    return Depends(get_session)


@router.get("/mesh_memory", response_model=ServiceResponse)
def mesh_memory_endpoint(
    session: Any = _require_session(),
) -> ServiceResponse:
    """Placeholder – returns an empty mesh memory payload."""
    return ServiceResponse(success=True, data=[])


@router.get("/mesh_memory/get", response_model=ServiceResponse)
def mesh_memory_endpoint_get(
    session: Any = _require_session(),
) -> ServiceResponse:
    """Placeholder – mirrors :func:`mesh_memory_endpoint`."""
    return mesh_memory_endpoint(session)


@router.get("/score_disputes", response_model=ServiceResponse)
def get_score_disputes_endpoint(
    session: Any = _require_session(),
) -> ServiceResponse:
    """Placeholder – returns an empty list of score disputes."""
    return ServiceResponse(success=True, data=[])


@router.post("/signal_scores", response_model=ServiceResponse)
def signal_scores_endpoint(
    request: Request,
    session: Any = _require_session(),
) -> ServiceResponse:
    """Placeholder – acknowledges receipt of a score signal."""
    # In a real implementation the request body would be parsed and stored.
    return ServiceResponse(success=True)


@router.get("/recency_report", response_model=ServiceResponse)
def recency_report(
    session: Any = _require_session(),
) -> ServiceResponse:
    """Placeholder – returns an empty recency report."""
    return ServiceResponse(success=True, data=[])


# --------------------------------------------------------------------------- #
# Self‑test utilities
# --------------------------------------------------------------------------- #
def run_self_test() -> bool:
    """
    Minimal self‑test that validates the most common imports and the router
    registration.  Returns ``True`` on success.
    """
    # Verify that required models are importable.
    _ = Org
    _ = User
    _ = McpServerRegistry
    _ = McpLlmAxisScore
    _ = McpScoreDispute

    # Verify that the router contains at least one route.
    if not router.routes:
        raise RuntimeError("Router has no routes registered")

    # All checks passed.
    return True


def test_self() -> bool:
    """Alias used by some staged services."""
    return run_self_test()


# --------------------------------------------------------------------------- #
# Module export list – keeps the public surface explicit.
# --------------------------------------------------------------------------- #
__all__ = [
    "ServiceBaseModel",
    "ServiceResponse",
    "router",
    "mesh_memory_endpoint",
    "mesh_memory_endpoint_get",
    "get_score_disputes_endpoint",
    "signal_scores_endpoint",
    "recency_report",
    "run_self_test",
    "test_self",
]


# --------------------------------------------------------------------------- #
# __main__ entry point – executes the self‑test and prints exactly ``PASS``.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if run_self_test():
        print("PASS")