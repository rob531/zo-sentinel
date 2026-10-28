from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_staleness

router = APIRouter(prefix="/api")


@router.get("/registry/staleness")
def registry_staleness(
    days: int = Query(..., ge=0),
    session: Session = Depends(get_session),
):
    """
    Return a staleness view of the MCP server registry.

    Parameters
    ----------
    days: int
        Number of days to look back for staleness calculations.
    session: Session
        Database session provided by FastAPI dependency injection.

    Returns
    -------
    dict
        The staleness payload produced by ``logic.get_staleness``.
    """
    return get_staleness(days=days, session=session)