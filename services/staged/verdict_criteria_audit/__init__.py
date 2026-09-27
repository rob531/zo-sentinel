"""
zo-sentinel service package core.

Provides a minimal FastAPI router, a DB dependency helper, and a base class
that other staged services can inherit.  The implementation is deliberately
lightweight – it does not perform any DB queries itself, allowing the
self‑test to run without a real database connection.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# Application DB session and models – required for all services.
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    User,
    Org,
)

__all__ = ["router", "BaseService", "get_db"]


# ----------------------------------------------------------------------
# Router & dependency
# ----------------------------------------------------------------------
router = APIRouter()


def get_db(session: Session = Depends(get_session)) -> Session:
    """FastAPI dependency that provides a SQLAlchemy session."""
    return session


# ----------------------------------------------------------------------
# Base service class
# ----------------------------------------------------------------------
class BaseService:
    """
    Base class for staged services.

    Sub‑classes can attach their own routes to ``self.router`` or use the
    module‑level ``router`` directly.  The class supplies the ``get_db``
    dependency and a convenient ``raise_not_found`` helper.
    """

    router: APIRouter = router

    @staticmethod
    def raise_not_found(detail: str = "Item not found") -> None:
        """Utility to raise a 404 HTTPException."""
        raise HTTPException(status_code=404, detail=detail)


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    from fastapi import FastAPI

    # Create a minimal FastAPI app and mount the router.
    test_app = FastAPI()
    test_app.include_router(router)

    # If we reach this point the module imported correctly and the router
    # was attached without touching the database.
    print("PASS")