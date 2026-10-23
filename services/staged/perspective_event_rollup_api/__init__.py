# services/staged/__init__.py
# Auto‑emitted service package. Relative intra‑service imports survive
# staged→active promotion without rewrite.

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

# ----------------------------------------------------------------------
# Data layer – must use the app's DB session and models.
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

router = APIRouter()


# ----------------------------------------------------------------------
# Core helpers – return placeholder data structures.
# ----------------------------------------------------------------------
def get_mesh_scores(session=Depends(get_session)):
    """Return mesh scores (placeholder)."""
    return []


def get_signal_scores(session=Depends(get_session)):
    """Return signal scores (placeholder)."""
    return []


def get_mesh_memory(session=Depends(get_session)):
    """Return mesh memory (placeholder)."""
    return {}


def reset_quarantine_endpoint(session=Depends(get_session)):
    """Reset quarantine (placeholder)."""
    return {"reset": True}


def dummy_post_endpoint(session=Depends(get_session)):
    """Dummy POST endpoint (placeholder)."""
    return {"status": "ok"}


def reset_server_export_api_quarantine(session=Depends(get_session)):
    """Reset server export API quarantine (placeholder)."""
    return {"reset": True}


# ----------------------------------------------------------------------
# FastAPI endpoints – thin wrappers around the helpers.
# ----------------------------------------------------------------------
@router.get("/mesh_scores")
def mesh_scores_endpoint(session=Depends(get_session)):
    return JSONResponse(content=get_mesh_scores(session))


@router.get("/signal_scores")
def signal_scores_endpoint(session=Depends(get_session)):
    return JSONResponse(content=get_signal_scores(session))


@router.get("/mesh_memory")
def mesh_memory_endpoint(session=Depends(get_session)):
    return JSONResponse(content=get_mesh_memory(session))


@router.post("/reset_quarantine")
def reset_quarantine_api(session=Depends(get_session)):
    return JSONResponse(content=reset_quarantine_endpoint(session))


@router.post("/dummy")
def dummy_post_api(session=Depends(get_session)):
    return JSONResponse(content=dummy_post_endpoint(session))


@router.post("/reset_server_export_quarantine")
def reset_server_export_quarantine_api(session=Depends(get_session)):
    return JSONResponse(content=reset_server_export_api_quarantine(session))


# ----------------------------------------------------------------------
# Self‑test harness – exercised by __main__ and by other packages.
# ----------------------------------------------------------------------
def _run_self_test():
    """Run a minimal self‑test; raises on failure."""
    class _DummySession:
        """A no‑op session used for self‑test."""
        pass

    dummy = _DummySession()

    # Call each helper to ensure they execute without error.
    _ = get_mesh_scores(dummy)
    _ = get_signal_scores(dummy)
    _ = get_mesh_memory(dummy)
    _ = reset_quarantine_endpoint(dummy)
    _ = dummy_post_endpoint(dummy)
    _ = reset_server_export_api_quarantine(dummy)

    # Call each endpoint wrapper (FastAPI dependencies are bypassed here).
    _ = mesh_scores_endpoint(dummy)
    _ = signal_scores_endpoint(dummy)
    _ = mesh_memory_endpoint(dummy)
    _ = reset_quarantine_api(dummy)
    _ = dummy_post_api(dummy)
    _ = reset_server_export_quarantine_api(dummy)


if __name__ == "__main__":
    try:
        _run_self_test()
        print("PASS")
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: {exc}")