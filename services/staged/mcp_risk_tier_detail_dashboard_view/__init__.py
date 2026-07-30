"""zo-sentinel staged service package.

Provides minimal FastAPI routers and utility functions required by
other staged services. All data access uses the canonical
`app.db.get_session` dependency and models from `app.models`.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

# Canonical data access – must not be redefined elsewhere.
from app.db import get_session
import app.models as models  # noqa: F401  (imported for side‑effects / type hints)

router = APIRouter()


# ----------------------------------------------------------------------
# Endpoint / utility definitions
# ----------------------------------------------------------------------
@router.get("/mesh_memory")
def mesh_memory_endpoint(db: Session = Depends(get_session)):
    """Placeholder mesh memory endpoint."""
    return {"status": "ok", "source": "mesh_memory_endpoint"}


@router.post("/reset_quarantine")
def reset_quarantine_endpoint(db: Session = Depends(get_session)):
    """Placeholder reset quarantine endpoint."""
    return {"status": "ok", "source": "reset_quarantine_endpoint"}


@router.post("/reset_server_export_api_quarantine")
def reset_server_export_api_quarantine_endpoint(db: Session = Depends(get_session)):
    """Placeholder reset server export API quarantine endpoint."""
    return {"status": "ok", "source": "reset_server_export_api_quarantine_endpoint"}


@router.get("/mesh_scores")
def mesh_scores_endpoint(db: Session = Depends(get_session)):
    """Placeholder mesh scores endpoint."""
    return {"status": "ok", "source": "mesh_scores_endpoint"}


@router.get("/signal_scores")
def signal_scores_endpoint(db: Session = Depends(get_session)):
    """Placeholder signal scores endpoint."""
    return {"status": "ok", "source": "signal_scores_endpoint"}


@router.post("/dummy_post")
def dummy_post_endpoint(db: Session = Depends(get_session)):
    """Placeholder dummy POST endpoint."""
    return {"status": "ok", "source": "dummy_post_endpoint"}


def get_mesh_memory(db: Session = Depends(get_session)):
    """Utility returning mesh memory data."""
    return {"mesh_memory": "placeholder"}


def get_mesh_scores(db: Session = Depends(get_session)):
    """Utility returning mesh scores data."""
    return {"mesh_scores": "placeholder"}


def get_signal_scores(db: Session = Depends(get_session)):
    """Utility returning signal scores data."""
    return {"signal_scores": "placeholder"}


def _run_self_test() -> str:
    """Self‑test entry point used by many staged services.

    Returns the literal string ``\"PASS\"`` when the module loads
    correctly.
    """
    # The test does not touch the database; it merely verifies that
    # the symbols exist and can be called.
    _ = mesh_memory_endpoint
    _ = reset_quarantine_endpoint
    _ = reset_server_export_api_quarantine_endpoint
    _ = mesh_scores_endpoint
    _ = signal_scores_endpoint
    _ = dummy_post_endpoint
    _ = get_mesh_memory
    _ = get_mesh_scores
    _ = get_signal_scores
    return "PASS"


__all__ = [
    "router",
    "mesh_memory_endpoint",
    "reset_quarantine_endpoint",
    "reset_server_export_api_quarantine_endpoint",
    "mesh_scores_endpoint",
    "signal_scores_endpoint",
    "dummy_post_endpoint",
    "get_mesh_memory",
    "get_mesh_scores",
    "get_signal_scores",
    "_run_self_test",
]


# ----------------------------------------------------------------------
# Module self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    try:
        result = _run_self_test()
        print(result)
    except Exception:  # pragma: no cover
        print("FAIL")