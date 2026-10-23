from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_growth_progress

router = APIRouter(prefix="/api", tags=["registry_growth_progress"])


@router.get("/registry/growth")
def registry_growth_progress_endpoint(session: Session = Depends(get_session)):
    """Return daily registry growth statistics."""
    return get_registry_growth_progress(session)