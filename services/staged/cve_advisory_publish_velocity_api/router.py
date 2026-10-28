from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_publish_velocity

router = APIRouter(prefix="/api", tags=["cve_advisory_publish_velocity"])


@router.get("/cve/advisories/publish-velocity")
def publish_velocity(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
):
    """
    Return publish velocity and link coverage statistics for CVE advisories.

    Parameters
    ----------
    days: int
        Number of days to look back (default 30, clamped to 1‑365).
    session: Session
        Database session injected by FastAPI dependency.

    Returns
    -------
    dict
        JSON payload matching the service contract.
    """
    return get_publish_velocity(session, days)