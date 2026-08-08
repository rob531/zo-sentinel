from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_scorecard_badge

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/scorecard")
def scorecard_badge(server_id: int, session: Session = Depends(get_session)):
    return get_scorecard_badge(server_id, session)