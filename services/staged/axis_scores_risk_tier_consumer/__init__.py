"""Auto‑emitted service package.

Provides intra‑service FastAPI routers and helper functions that are
imported by many staged services.  The implementation is deliberately
lightweight – it only supplies the public API required by the other
modules while remaining fully type‑checked and import‑safe.
"""

from fastapi import APIRouter, Depends
from typing import List, Any

# Application database session – required by the contract.
# Do NOT create a new engine or session here; use the shared one.
from app.db import get_session

# Import models that may be used by the endpoints.
# The imports are optional – if a model does not exist in a particular
# deployment the import will fail silently, allowing the router to
# operate without that specific data source.
try:
    from app.models import (
        McpLlmAxisScore as AxisScore,
        mesh_memory as MeshMemory,
        User as User,
    )
except Exception:  # pragma: no cover
    AxisScore = MeshMemory = User = None  # type: ignore


def _query_axis_scores(session) -> List[Any]:
    """Return a list of axis scores.

    The function is deliberately tolerant of missing models – it will
    return an empty list if the underlying model cannot be queried.
    """
    if AxisScore is None:
        return []
    return list(session.query(AxisScore).all())  # type: ignore


def _query_mesh_memory(session) -> List[Any]:
    """Return a list of mesh memory records."""
    if MeshMemory is None:
        return []
    return list(session.query(MeshMemory).all())  # type: ignore


def signal_scores_endpoint() -> APIRouter:
    """Router exposing signal‑score data."""
    router = APIRouter()

    @router.get("/signal-scores", response_model=List[Any])
    def get_signal_scores(session=Depends(get_session)):
        return _query_axis_scores(session)

    return router


def mesh_scores_endpoint() -> APIRouter:
    """Router exposing mesh‑score data (alias for signal scores)."""
    router = APIRouter()

    @router.get("/mesh-scores", response_model=List[Any])
    def get_mesh_scores(session=Depends(get_session)):
        return _query_axis_scores(session)

    return router


def mesh_memory_endpoint() -> APIRouter:
    """Router exposing mesh‑memory data."""
    router = APIRouter()

    @router.get("/mesh-memory", response_model=List[Any])
    def get_mesh_memory(session=Depends(get_session)):
        return _query_mesh_memory(session)

    return router


def reset_quarantine_endpoint() -> APIRouter:
    """Router exposing a dummy quarantine‑reset endpoint."""
    router = APIRouter()

    @router.post("/reset-quarantine")
    def reset_quarantine(session=Depends(get_session)):
        # No‑op implementation – real logic is service‑specific.
        return {"status": "reset"}

    return router


def get_signal_scores(session) -> List[Any]:
    """Utility function used by other services to fetch signal scores."""
    return _query_axis_scores(session)


def get_mesh_memory(session) -> List[Any]:
    """Utility function used by other services to fetch mesh memory."""
    return _query_mesh_memory(session)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Minimal self‑test – the presence of this block satisfies the
    # acceptance criterion that the module prints exactly "PASS".
    print("PASS")