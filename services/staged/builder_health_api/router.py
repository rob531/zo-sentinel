from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import BuilderHealthResponse, get_builder_health

router = APIRouter(prefix="/api", tags=["builder_health"])

@router.get(
    "/builder/health",
    response_model=BuilderHealthResponse,
    summary="Health status of the Zo Sentinel builder",
)
def builder_health(session: Session = Depends(get_session)):
    """
    Return a health snapshot for the builder service.

    The heavy‑lifting is performed in ``services.staged.builder_health_api.logic``.
    """
    return get_builder_health(session)