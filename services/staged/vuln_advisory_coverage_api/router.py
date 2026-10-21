from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_advisories, get_advisory_links

router = APIRouter(prefix="/api")


@router.get("/vulns/advisories")
def list_advisories(
    feed: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    ecosystem: str | None = Query(default=None),
    days: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
):
    """
    Retrieve advisory coverage information with optional filters.
    """
    return get_advisories(
        session,
        feed=feed,
        severity=severity,
        ecosystem=ecosystem,
        days=days,
        limit=limit,
    )


@router.get("/vulns/advisories/{advisory_id}/links")
def advisory_links(
    advisory_id: int,
    session: Session = Depends(get_session),
):
    """
    Retrieve link information for a specific advisory.
    """
    return get_advisory_links(session, advisory_id)