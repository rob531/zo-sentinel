"""
Central utilities for staged services.

Provides a stable import surface for intra‑service modules such as
`services/staged/family_coverage/__init__.py`, `services/staged/scoring_consumer/__init__.py`,
etc.  All data access is performed through the application‑wide SQLAlchemy session
(`app.db.get_session`) and the models defined in `app.models`.
"""

from __future__ import annotations

from typing import Any, List

from app.db import get_session
from app.models import SignalScore, MeshMemory, MeshScore  # type: ignore

__all__ = [
    "get_signal_scores",
    "get_mesh_memory",
    "get_mesh_scores",
    "dummy_post_endpoint",
    "_run_self_test",
    "mesh_memory_endpoint",
    "reset_quarantine_endpoint",
    "_dummy_post",
    "reset_server_export_api_quarantine",
]


def get_signal_scores() -> List[SignalScore]:
    """Return all rows from the `SignalScore` table."""
    with get_session() as session:
        return session.query(SignalScore).all()


def get_mesh_memory() -> List[MeshMemory]:
    """Return all rows from the `MeshMemory` table."""
    with get_session() as session:
        return session.query(MeshMemory).all()


def get_mesh_scores() -> List[MeshScore]:
    """Return all rows from the `MeshScore` table."""
    with get_session() as session:
        return session.query(MeshScore).all()


def dummy_post_endpoint(payload: dict) -> dict:
    """Echo endpoint used by a few services for testing."""
    return {"received": payload}


def _run_self_test() -> dict:
    """
    Minimal self‑test exercised by the package's ``__main__`` block.

    It simply verifies that the DB session can be opened and that the three
    query helpers return iterables (which may be empty).  The test does not
    depend on any particular data being present.
    """
    scores = get_signal_scores()
    memory = get_mesh_memory()
    mesh = get_mesh_scores()
    return {
        "signal_scores_count": len(scores),
        "mesh_memory_count": len(memory),
        "mesh_scores_count": len(mesh),
    }


def mesh_memory_endpoint() -> List[MeshMemory]:
    """Endpoint wrapper that forwards to :func:`get_mesh_memory`."""
    return get_mesh_memory()


def reset_quarantine_endpoint() -> dict:
    """Placeholder for a quarantine‑reset endpoint."""
    return {"status": "quarantine reset"}


def _dummy_post() -> dict:
    """Another minimal echo used by a subset of services."""
    return {"status": "ok"}


def reset_server_export_api_quarantine() -> dict:
    """Placeholder for the server‑export‑API quarantine reset."""
    return {"status": "server export quarantine reset"}


if __name__ == "__main__":
    try:
        _run_self_test()
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print("FAIL")
        raise exc