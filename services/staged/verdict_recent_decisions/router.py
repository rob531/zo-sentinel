from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import fetch_recent_verdicts, RecentVerdictsResponse

router = APIRouter(prefix="/api", tags=["verdict_recent_decisions"])


@router.get(
    "/verdicts/recent",
    response_model=RecentVerdictsResponse,
    summary="Retrieve recent per‑server verdict decisions",
)
def get_recent_verdicts(
    verdict: str | None = Query(
        None,
        description="Filter results by verdict label (e.g., APPROVED, REJECTED)",
    ),
    tier: str | None = Query(
        None,
        description="Filter results by risk tier (e.g., TRUSTED_GENERAL)",
    ),
    days: int = Query(
        30,
        ge=1,
        description="Number of days in the past to include",
    ),
    page: int = Query(
        1,
        ge=1,
        description="Page number for pagination",
    ),
    page_size: int = Query(
        20,
        ge=1,
        le=100,
        description="Number of items per page",
    ),
    session: Session = Depends(get_session),
):
    """
    Thin wrapper that forwards request parameters to the business‑logic layer.
    """
    return fetch_recent_verdicts(
        session=session,
        verdict=verdict,
        tier=tier,
        days=days,
        page=page,
        page_size=page_size,
    )