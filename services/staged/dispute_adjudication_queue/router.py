"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_dispute_page

router = APIRouter(prefix="/api", tags=["dispute_adjudication_queue"])


class DisputeSummary(BaseModel):
    """A single dispute record returned by the API."""

    id: int
    server_id: int
    server_name: Optional[str] = None
    submitted_by: int
    proposed_overall_risk: int
    proposed_axes: Dict[str, Any] = Field(default_factory=dict)
    reason_category: str
    explanation: str
    status: str
    admin_note: Optional[str] = None
    created_at: str
    resolved_at: Optional[str] = None


class StatusCount(BaseModel):
    """Count of disputes per status."""

    status: str
    count: int


class DisputePage(BaseModel):
    """Paginated response for dispute listings."""

    total: int
    count: int
    offset: int
    disputes: List[DisputeSummary]
    status_counts: List[StatusCount]


@router.get(
    "/disputes",
    response_model=DisputePage,
    summary="List disputes with pagination and optional status filter",
)
def list_disputes(
    status: str = Query("OPEN", description="Filter by dispute status"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of rows to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_session),
) -> DisputePage:
    """
    Retrieve a paginated list of disputes.

    The heavy lifting is performed in `services.staged.dispute_adjudication_queue.logic`
    which returns a `DisputePage` compatible object.
    """
    page = get_dispute_page(db, status=status, limit=limit, offset=offset)
    return page