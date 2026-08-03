from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import DisputeSummary, get_dispute_by_id, get_disputes

router = APIRouter(prefix="/api", tags=["score_dispute_summary"])


@router.get(
    "/disputes",
    response_model=List[DisputeSummary],
    summary="List score disputes",
)
def list_disputes(
    response: Response,
    status: Optional[str] = Query(
        None,
        regex="^(PENDING|APPROVED|REJECTED)$",
        description="Filter by dispute status",
    ),
    server_id: Optional[int] = Query(
        None,
        description="Filter by server identifier",
    ),
    limit: int = Query(
        50,
        ge=1,
        description="Maximum number of records to return",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Number of records to skip",
    ),
    db: Session = Depends(get_session),
):
    """
    Retrieve a paginated list of score disputes, optionally filtered by status
    and/or server. The total number of matching records is returned in the
    `total_count` response header.
    """
    result = get_disputes(
        db,
        status=status,
        server_id=server_id,
        limit=limit,
        offset=offset,
    )
    response.headers["total_count"] = str(result.total_count)
    return result.items


@router.get(
    "/disputes/{dispute_id}",
    response_model=DisputeSummary,
    summary="Get a single score dispute",
)
def get_dispute(
    dispute_id: int,
    db: Session = Depends(get_session),
):
    """
    Retrieve a single dispute by its identifier.
    """
    dispute = get_dispute_by_id(db, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="Dispute not found")
    return dispute