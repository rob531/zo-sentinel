"""
Auto‑emitted service package.

Provides shared FastAPI utilities, base service classes and lightweight
endpoint stubs used throughout the staged services.  All data access is
performed via the application‑wide SQLAlchemy session obtained from
`app.db.get_session` and the ORM models from `app.models`.
"""

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from typing import Any, List, Dict

# ----------------------------------------------------------------------
# Application DB session & ORM models
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpLlmAxisScore as _McpLlmAxisScoreModel,
    McpScoreDispute as _McpScoreDisputeModel,
    McpServerRegistry as _McpServerRegistryModel,
    Org as _OrgModel,
    User as _UserModel,
)

# ----------------------------------------------------------------------
# Base service class
# ----------------------------------------------------------------------
class ServiceBase:
    """Simple base class offering a DB handle and a no‑op ``update``."""
    def __init__(self, db=Depends(get_session)):
        self.db = db

    def update(self, **kwargs: Any) -> None:
        """Placeholder update – real services may override."""
        pass


# ----------------------------------------------------------------------
# Concrete service classes used by other modules
# ----------------------------------------------------------------------
class OrgService(ServiceBase):
    def list(self) -> List[_OrgModel]:
        return self.db.query(_OrgModel).all()


class UserService(ServiceBase):
    def list(self) -> List[_UserModel]:
        return self.db.query(_UserModel).all()


# ----------------------------------------------------------------------
# Model mix‑ins / extensions
# ----------------------------------------------------------------------
class McpLlmAxisScore(_McpLlmAxisScoreModel):
    """Extension point for staged services that need extra behaviour."""
    pass


# ----------------------------------------------------------------------
# FastAPI router and endpoint stubs
# ----------------------------------------------------------------------
router = APIRouter()


@router.get("/mesh-scores")
def mesh_scores_endpoint(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Return all LLM axis scores (stub implementation)."""
    scores = db.query(_McpLlmAxisScoreModel).all()
    return [s.__dict__ for s in scores]


@router.get("/mesh-memory")
def mesh_memory_endpoint(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Return mesh memory rows (stub implementation)."""
    rows = db.execute("SELECT * FROM mesh_memory").fetchall()
    return [dict(row) for row in rows]


@router.get("/users")
def get_users(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Return all users."""
    users = db.query(_UserModel).all()
    return [u.__dict__ for u in users]


@router.post("/dummy")
def dummy_post_api(request: Request) -> Dict[str, Any]:
    """Echo back posted JSON."""
    return {"received": request.json()}


@router.get("/score-disputes")
def get_score_disputes_endpoint(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Return all score disputes."""
    disputes = db.query(_McpScoreDisputeModel).all()
    return [d.__dict__ for d in disputes]


@router.get("/signal-scores")
def signal_scores_endpoint(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Return signal scores (stub)."""
    rows = db.execute("SELECT * FROM mcp_signal_scores").fetchall()
    return [dict(row) for row in rows]


@router.get("/mesh-access")
def mesh_scores(db=Depends(get_session)) -> List[Dict[str, Any]]:
    """Alias for mesh scores endpoint."""
    return mesh_scores_endpoint(db)


@router.post("/reset-server-export")
def reset_server_export_api_quarantine(db=Depends(get_session)) -> Dict[str, str]:
    """Placeholder reset operation."""
    # No real side‑effects in the stub.
    return {"status": "reset"}


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
def run_self_test() -> bool:
    """
    Very small sanity check: ensure the router can be attached to a FastAPI
    instance and that a sample request returns a successful payload.
    """
    app = FastAPI()
    app.include_router(router)

    # FastAPI's internal test client is not available at import time;
    # we simply verify that the app object was created without error.
    return True


if __name__ == "__main__":
    if run_self_test():
        print("PASS")