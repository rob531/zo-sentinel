from typing import Optional

from fastapi import APIRouter, Depends, Query
from app.db import get_session

# The logic module provides the core implementation.
# It must expose a callable named `get` that accepts
# (session, feed: Optional[str], stale_hours: int) and returns
# a JSON‑serialisable dict matching the response contract.
from .logic import get

router = APIRouter(prefix="/api")


@router.get(
    "/vuln/advisories/lag",
    summary="Report advisory ingestion lag and staleness per feed",
)
def vuln_advisory_refresh_lag(
    feed: Optional[str] = Query(
        default=None,
        description="Filter to a single advisory feed name",
    ),
    stale_hours: int = Query(
        default=48,
        ge=0,
        description="Number of hours after which an advisory is considered stale",
    ),
    session=Depends(get_session),
):
    """
    Return lag statistics for vulnerability advisories.

    Delegates the heavy lifting to the service's logic layer.
    """
    return get(session, feed, stale_hours)