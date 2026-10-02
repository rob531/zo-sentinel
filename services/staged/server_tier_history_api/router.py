from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_tier_history

router = APIRouter(prefix="/api", tags=["server_tier_history"])


@router.get("/servers/{server_id}/tier-history")
def tier_history(
    server_id: int,
    session: Session = Depends(get_session),
):
    """
    Retrieve the tier‑history for a given server.
    """
    result = get_server_tier_history(server_id, session)
    if result is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return result