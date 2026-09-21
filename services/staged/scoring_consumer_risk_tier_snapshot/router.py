from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_risk_tier_snapshot

router = APIRouter()


@router.get("/api/servers/{server_id}/tier-snapshot")
def tier_snapshot(server_id: int, db: Session = Depends(get_session)):
    return get_server_risk_tier_snapshot(server_id, db)