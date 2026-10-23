# services/staged/trust_tier_computation/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_trust_tier_logic

router = APIRouter(prefix="/api", tags=["trust-tier"])


@router.get("/servers/{server_id}/trust-tier")
async def get_server_trust_tier(
    server_id: int,
    session: Session = Depends(get_session)
):
    """
    Get trust tier information for a specific server.
    
    Returns server details including trust status, overall risk, and per-axis trust status.
    """
    return get_trust_tier_logic(session, server_id)