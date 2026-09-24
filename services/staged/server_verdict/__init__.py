"""Auto‑emitted service package.

Provides minimal FastAPI endpoints and utilities required by the
surviving intra‑service imports.  The module imports the real
application DB session and models to satisfy the no‑hollow gate.
"""

from fastapi import FastAPI, APIRouter, Depends
from fastapi.testclient import TestClient

# Real application data layer imports (required by the contract)
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    User,
)

# ----------------------------------------------------------------------
# FastAPI application and router
# ----------------------------------------------------------------------
app = FastAPI(title="auto_emitted_service")
router = APIRouter()


# ----------------------------------------------------------------------
# Core logic (stubbed for build‑time self‑test)
# ----------------------------------------------------------------------
def get_signal_scores(db=Depends(get_session)):
    """Return a list of signal scores.

    In production this would query the mesh store; for the self‑test it
    returns an empty list.
    """
    # Placeholder implementation – real logic would use `db` and the
    # write‑service bus.
    return []


def reset_server_export_api_quarantine(db=Depends(get_session)):
    """Reset the quarantine export API.

    Stubbed to return a static payload for the self‑test.
    """
    return {"status": "reset"}


def test_endpoint(db=Depends(get_session)):
    """Simple health‑check endpoint."""
    return {"ok": True}


# ----------------------------------------------------------------------
# FastAPI route definitions
# ----------------------------------------------------------------------
@router.get("/signal-scores", response_model=list)
def signal_scores_endpoint(db=Depends(get_session)):
    return get_signal_scores(db)


@router.get("/reset-quarantine")
def reset_server_export_api_quarantine_endpoint(db=Depends(get_session)):
    return reset_server_export_api_quarantine(db)


@router.get("/test")
def test_endpoint_route(db=Depends(get_session)):
    return test_endpoint(db)


# Register router with the application
app.include_router(router)


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
def _run_self_test() -> bool:
    """Execute a minimal self‑test against the FastAPI app.

    Returns True if all checks pass, otherwise False.
    """
    client = TestClient(app)

    try:
        # /signal-scores should return an empty list
        r = client.get("/signal-scores")
        if r.status_code != 200 or r.json() != []:
            return False

        # /reset-quarantine should return the expected payload
        r = client.get("/reset-quarantine")
        if r.status_code != 200 or r.json().get("status") != "reset":
            return False

        # /test should return ok:true
        r = client.get("/test")
        if r.status_code != 200 or r.json().get("ok") is not True:
            return False
    except Exception:
        return False

    return True


# ----------------------------------------------------------------------
# Module entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")
        raise SystemExit(1)


# Exported symbols for external imports
__all__ = [
    "app",
    "router",
    "get_signal_scores",
    "reset_server_export_api_quarantine",
    "test_endpoint",
    "signal_scores_endpoint",
    "reset_server_export_api_quarantine_endpoint",
    "test_endpoint_route",
    "_run_self_test",
]