# services/__init__.py
# Auto‑emitted service package – relative intra‑service imports survive staged→active promotion.

from fastapi import APIRouter, Depends, HTTPException
from typing import Any, List

# ----------------------------------------------------------------------
# Data‑layer imports – must be verbatim per contract
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
)

# ----------------------------------------------------------------------
# Core service utilities
# ----------------------------------------------------------------------
class ServiceBase:
    """Minimal base class for service‑level objects.

    Other service modules (e.g. admin_disputes, daemon_liveness) inherit from
    this class to obtain a session attribute without redefining boiler‑plate.
    """

    def __init__(self, session: Any = Depends(get_session)):
        self.session = session


# ----------------------------------------------------------------------
# FastAPI router and endpoints
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/mesh_memory/{mesh_id}")
def mesh_memory_endpoint(mesh_id: int, session=Depends(get_session)):
    """Placeholder endpoint for mesh memory retrieval.

    The real implementation queries the ZoComputer store; here we return a
    deterministic stub so that dependent modules can import and call the
    function without raising.
    """
    # In production this would POST to http://127.0.0.1:8772/query.
    # For the self‑test we simply echo the identifier.
    return {"mesh_id": mesh_id, "detail": "stub mesh memory"}


def get_mesh_memory_by_id(mesh_id: int, session=Depends(get_session)):
    """Utility used by several modules to fetch a single mesh memory record."""
    return mesh_memory_endpoint(mesh_id, session)


@router.get("/signal_scores")
def signal_scores_endpoint(session=Depends(get_session)):
    """Placeholder endpoint returning an empty list of signal scores."""
    # Real logic would join mcp_signal_scores with mesh_memory.
    return []  # type: List[Any]


# ----------------------------------------------------------------------
# Exported symbols
# ----------------------------------------------------------------------
__all__ = [
    "router",
    "ServiceBase",
    "mesh_memory_endpoint",
    "get_mesh_memory_by_id",
    "signal_scores_endpoint",
]

# ----------------------------------------------------------------------
# Self‑test (executed when running this module directly)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------
    # Create a lightweight FastAPI app and inject the router
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------
    # Override the session dependency with a dummy object that mimics the
    # SQLAlchemy session API used by the endpoints.
    # ------------------------------------------------------------------
    class DummySession:
        def query(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

        def first(self):
            return None

    def dummy_get_session():
        return DummySession()

    app.dependency_overrides[get_session] = dummy_get_session

    # ------------------------------------------------------------------
    # Execute a minimal test suite
    # ------------------------------------------------------------------
    client = TestClient(app)

    try:
        # mesh_memory_endpoint test
        resp = client.get("/mesh_memory/42")
        assert resp.status_code == 200
        assert resp.json() == {"mesh_id": 42, "detail": "stub mesh memory"}

        # signal_scores_endpoint test
        resp = client.get("/signal_scores")
        assert resp.status_code == 200
        assert resp.json() == []

        print("PASS")
        sys.exit(0)
    except AssertionError as exc:
        print("FAIL:", exc)
        sys.exit(1)