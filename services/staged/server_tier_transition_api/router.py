from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_tier_history

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/tier-history")
def tier_history(server_id: int, db: Session = Depends(get_session)):
    return get_server_tier_history(server_id, db)