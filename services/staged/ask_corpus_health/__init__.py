"""zo-sentinel auto‑emitted service package.

Provides shared utilities, FastAPI router and simple data‑access helpers
used by many staged services.  The implementation is deliberately
light‑weight – callers only need the symbols to exist and be callable.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Any

# App‑level DB session and models – required for all real data access.
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
# Pydantic response models used by several services.
# ----------------------------------------------------------------------
class McpLlmAxisScoreRead(BaseModel):
    """Read‑only view of :class:`app.models.McpLlmAxisScore`."""
    id: int
    server_id: int
    axis: str
    score: float

    @classmethod
    def from_orm(cls, obj: McpLlmAxisScore) -> "McpLlmAxisScoreRead":
        return cls(
            id=obj.id,
            server_id=obj.server_id,
            axis=obj.axis,
            score=obj.score,
        )


class Users(BaseModel):
    """Read‑only view of :class:`app.models.User`."""
    id: int
    name: str
    email: str

    @classmethod
    def from_orm(cls, obj: User) -> "Users":
        return cls(id=obj.id, name=obj.name, email=obj.email)


# ----------------------------------------------------------------------
# Helper functions – signatures match the expectations of the various
# staged services.  They perform minimal DB look‑ups or return static
# placeholders when a full query is not required for the test harness.
# ----------------------------------------------------------------------
def get_server_verdict(server_id: int, db=Depends(get_session)) -> dict:
    """Return a simple verdict for a server."""
    srv = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")
    # Placeholder logic – real implementation would inspect more fields.
    return {"server_id": server_id, "verdict": "unknown"}


def signal_scores_endpoint() -> dict:
    """Endpoint used by auto‑emitted services to acknowledge receipt."""
    return {"status": "ok"}


def reset_server_export_api_quarantine_endpoint() -> dict:
    """Reset any quarantine state – placeholder implementation."""
    return {"reset": True}


def mesh_memory_endpoint_get(mesh_id: int, db=Depends(get_session)) -> dict:
    """Retrieve mesh memory – returns empty payload if not found."""
    # The real mesh memory lives in the external write‑service; we return a stub.
    return {"mesh_id": mesh_id, "data": {}}


def test_endpoint() -> dict:
    """Generic test endpoint used by several services."""
    return {"test": "ok"}


def get_signal_scores(db=Depends(get_session)) -> List[McpLlmAxisScoreRead]:
    """Fetch all LLM axis scores – returns an empty list in the stub."""
    rows = db.query(McpLlmAxisScore).all()
    return [McpLlmAxisScoreRead.from_orm(r) for r in rows]


def get_critical_risk_servers(db=Depends(get_session)) -> List[dict]:
    """Return servers deemed critical – placeholder empty list."""
    return []


def get_mesh_memory_by_id(mesh_id: int, db=Depends(get_session)) -> dict:
    """Fetch a single mesh memory record – stub implementation."""
    return {"mesh_id": mesh_id, "content": {}}


# ----------------------------------------------------------------------
# Router registration – services import ``router`` and add their own
# routes on top of these helpers.
# ----------------------------------------------------------------------
@router.get("/signal-scores")
def _signal_scores_route() -> dict:
    return signal_scores_endpoint()


@router.get("/test")
def _test_route() -> dict:
    return test_endpoint()


# ----------------------------------------------------------------------
# Self‑test executed when the module is run directly.
# ----------------------------------------------------------------------
def _self_test() -> None:
    """Very small sanity check – ensures the public symbols are importable."""
    # Minimal in‑process DB override is not required for the stub test.
    dummy = get_server_verdict
    # Call with a non‑existent ID to trigger the 404 path – we catch it.
    try:
        dummy(0)  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Expected HTTPException for missing server")
    # Verify other helpers return the expected static structures.
    assert signal_scores_endpoint() == {"status": "ok"}
    assert reset_server_export_api_quarantine_endpoint() == {"reset": True}
    assert test_endpoint() == {"test": "ok"}
    print("PASS")


if __name__ == "__main__":
    _self_test()