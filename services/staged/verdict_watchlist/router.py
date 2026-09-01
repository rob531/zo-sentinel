from fastapi import APIRouter, Depends, HTTPException, status
from app.db import get_session
from app.auth import get_current_user

from .logic import (
    get_watchlist,
    add_watchlist_item,
    delete_watchlist_item,
    get_risk_detail,
)
from .schemas import (
    WatchlistResponse,
    WatchlistCreate,
    RiskDetailResponse,
)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("/", response_model=WatchlistResponse)
def read_watchlist(
    current_user=Depends(get_current_user),
    db=Depends(get_session),
):
    """
    Return the watchlist for the organization of the current user.
    """
    return get_watchlist(db, org_id=current_user.org_id)


@router.post(
    "/",
    response_model=WatchlistCreate,
    status_code=status.HTTP_201_CREATED,
)
def create_watchlist_item(
    payload: WatchlistCreate,
    current_user=Depends(get_current_user),
    db=Depends(get_session),
):
    """
    Add a server to the watchlist (upsert).
    """
    return add_watchlist_item(
        db,
        server_id=payload.server_id,
        org_id=current_user.org_id,
        added_by=current_user.sub,
    )


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_watchlist_item_endpoint(
    server_id: int,
    current_user=Depends(get_current_user),
    db=Depends(get_session),
):
    """
    Remove a server from the watchlist.
    """
    delete_watchlist_item(
        db,
        server_id=server_id,
        org_id=current_user.org_id,
    )
    return None


@router.get(
    "/{server_id}/risk_detail",
    response_model=RiskDetailResponse,
)
def read_risk_detail(
    server_id: int,
    current_user=Depends(get_current_user),
    db=Depends(get_session),
):
    """
    Return the full risk breakdown for a watchlisted server.
    """
    return get_risk_detail(
        db,
        server_id=server_id,
        org_id=current_user.org_id,
    )