"""zo‑sentinel staged service package.

Provides minimal FastAPI endpoints used across the staged services.
All data access is via the application DB session; the self‑test
overrides the session with an in‑memory SQLite DB.
"""

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Application DB session – must be imported exactly as required.
from app.db import get_session
import app.models as models  # noqa: F401 – imported for side‑effects / type hints.

router = APIRouter()


@router.get("/signal_scores")
def signal_scores_endpoint(db: Session = Depends(get_session)):
    """Placeholder endpoint used by many staged services."""
    # Real implementation would query `models.McpLlmAxisScore` etc.
    return {"detail": "signal scores placeholder"}


@router.get("/mesh_memory")
def mesh_memory_endpoint(db: Session = Depends(get_session)):
    """Placeholder endpoint used by many staged services."""
    # Real implementation would query `models.mesh_memory` etc.
    return {"detail": "mesh memory placeholder"}


@router.get("/mesh_memory_endpoint")
def get_mesh_memory_endpoint(db: Session = Depends(get_session)):
    """Alias for mesh_memory_endpoint – kept for backward compatibility."""
    return mesh_memory_endpoint(db)


@router.get("/score_disputes")
def get_score_disputes_endpoint(db: Session = Depends(get_session)):
    """Placeholder endpoint for score disputes."""
    # Real implementation would query `models.McpScoreDispute`.
    return {"detail": "score disputes placeholder"}


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
def _run_self_test() -> bool:
    """Run a minimal self‑test against the FastAPI app.

    The test client uses the FastAPI app defined below; the test harness
    may override `get_session` with an in‑memory SQLite session.
    """
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Verify each endpoint returns HTTP 200.
    endpoints = [
        "/signal_scores",
        "/mesh_memory",
        "/mesh_memory_endpoint",
        "/score_disputes",
    ]

    for ep in endpoints:
        resp = client.get(ep)
        if resp.status_code != 200:
            return False
    return True


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")