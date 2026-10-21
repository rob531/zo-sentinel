from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_profile

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/profile")
def server_profile(
    server_id: str,
    session: Session = Depends(get_session),
):
    profile = get_server_profile(session, server_id)
    if not profile:
        raise HTTPException(status_code=404, detail="server not found")
    return profile