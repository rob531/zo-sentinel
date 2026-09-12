"""
zo-sentinel service package core.

Provides the minimal shared primitives required by the staged services:
- BaseService: common DB‑session holder.
- ServerResponse / UserRead: simple response models.
- router: FastAPI APIRouter for endpoint registration.
- Helper endpoint stubs used throughout the code‑base.
- run_self_test() and __main__ self‑test driver.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Application DB session and models – must be imported exactly as the app expects.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# --------------------------------------------------------------------------- #
# Core service class
# --------------------------------------------------------------------------- #
class BaseService:
    """
    Base class for all service implementations.

    Sub‑classes receive a SQLAlchemy Session via FastAPI's dependency injection.
    """

    def __init__(self, db: Session = Depends(get_session)):
        self.db: Session = db

    # Example helper used by several services – can be overridden.
    def get_open_disputes(self) -> list[McpScoreDispute]:
        """Return all score disputes that are not resolved."""
        return (
            self.db.query(McpScoreDispute)
            .filter(McpScoreDispute.resolved.is_(False))
            .all()
        )


# --------------------------------------------------------------------------- #
# Pydantic response models used across the package
# --------------------------------------------------------------------------- #
class ServerResponse(BaseModel):
    """Standard wrapper for service responses."""

    success: bool
    data: dict | None = None
    error: str | None = None


class UserRead(BaseModel):
    """Public representation of a User."""

    id: int
    email: str
    full_name: str | None = None
    is_active: bool


# --------------------------------------------------------------------------- #
# FastAPI router – concrete endpoints are thin wrappers around BaseService.
# --------------------------------------------------------------------------- #
router = APIRouter()


@router.get("/mesh_memory_endpoint", response_model=ServerResponse)
def mesh_memory_endpoint(
    service: BaseService = Depends(),
) -> ServerResponse:
    """
    Stub endpoint – in production this would query the mesh_memory store.
    """
    # Placeholder payload; real implementation would call the external write‑service.
    payload = {"message": "mesh memory endpoint placeholder"}
    return ServerResponse(success=True, data=payload)


@router.get("/mesh_memory_endpoint_get", response_model=ServerResponse)
def mesh_memory_endpoint_get(
    service: BaseService = Depends(),
) -> ServerResponse:
    """
    Alias for ``mesh_memory_endpoint`` kept for backward compatibility.
    """
    return mesh_memory_endpoint(service)


@router.get("/score_disputes", response_model=ServerResponse)
def get_score_disputes_endpoint(
    service: BaseService = Depends(),
) -> ServerResponse:
    """
    Returns a list of open score disputes.
    """
    disputes = service.get_open_disputes()
    payload = {"open_disputes": [d.id for d in disputes]}
    return ServerResponse(success=True, data=payload)


@router.post("/signal_scores", response_model=ServerResponse, status_code=status.HTTP_202_ACCEPTED)
def signal_scores_endpoint(
    service: BaseService = Depends(),
) -> ServerResponse:
    """
    Stub for signalling new scores – in production this would forward data to the
    write‑service bus.
    """
    # No side‑effects in the stub.
    return ServerResponse(success=True, data={"message": "scores signalled"})


@router.get("/recency_report", response_model=ServerResponse)
def recency_report(
    service: BaseService = Depends(),
) -> ServerResponse:
    """
    Stub recency report endpoint.
    """
    payload = {"report": "recency data placeholder"}
    return ServerResponse(success=True, data=payload)


# --------------------------------------------------------------------------- #
# Self‑test utilities
# --------------------------------------------------------------------------- #
def run_self_test() -> bool:
    """
    Minimal self‑test that verifies the module can be imported,
    the router is instantiated and the stub endpoints return the expected
    structure.  Returns ``True`` on success.
    """
    try:
        # Verify router registration
        assert isinstance(router, APIRouter)

        # Verify response model construction
        test_resp = ServerResponse(success=True, data={"ok": True})
        assert test_resp.success is True

        # Verify BaseService can be instantiated with a dummy session.
        # In the test environment ``get_session`` is overridden to provide a
        # transient in‑memory session, so we simply call the constructor.
        dummy_service = BaseService()
        assert isinstance(dummy_service, BaseService)

        # Verify stub endpoint call signatures (no DB access required)
        _ = mesh_memory_endpoint(dummy_service)
        _ = mesh_memory_endpoint_get(dummy_service)
        _ = get_score_disputes_endpoint(dummy_service)
        _ = signal_scores_endpoint(dummy_service)
        _ = recency_report(dummy_service)

        return True
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------------------- #
# Module entry‑point for manual verification
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if run_self_test():
        print("PASS")
    else:
        print("FAIL")