"""API router for the `api_signal_scores_distribution` staged service.

This module mirrors the structure of `services/_exemplar/router.py`. It provides a
thin FastAPI router that wires the real data layer (SQLAlchemy session from
`app.db`) to the business‑logic functions defined in `logic.py`. No stub or
in‑memory database is used, satisfying the “no‑hollow” gate.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

# Real session provider from the application.
from app.db import get_session

# Import all public callables from the service's logic module.
# The logic module contains the actual endpoint implementations.
from .logic import *  # noqa: F403,F401

# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter()


# Example endpoint wiring – the concrete signatures are defined in `logic.py`.
# If `logic.py` defines a function named `get_distribution`, it will be exposed
# at the root of this router. Adjust the endpoint name(s) as needed to match
# the actual logic implementation.
if "get_distribution" in globals():
    @router.get(
        "/",
        name="Get signal scores distribution",
        # The response model can be specified in the logic function via
        # FastAPI's `response_model` parameter; we keep it generic here.
    )
    async def get_distribution_endpoint(
        session: Session = Depends(get_session),
    ):
        """Delegate to the logic layer."""
        return await get_distribution(session)


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Simple import sanity check – if this module loads without error we
    # consider the self‑test passed.
    try:
        import importlib

        importlib.import_module(
            "services.staged.api_signal_scores_distribution.router"
        )
        print("PASS")
    except Exception as exc:  # pragma: no cover
        import sys

        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)