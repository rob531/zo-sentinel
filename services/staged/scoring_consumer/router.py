from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_scoring, get_scoring_batch

router = APIRouter()


@router.get("/api/scoring/{server_id}")
async def scoring_endpoint(
    server_id: str,
    db: Session = Depends(get_session),
):
    """
    Retrieve the risk tier scoring for a single server.
    """
    return await get_scoring(server_id, db)


@router.get("/api/scoring/batch")
async def scoring_batch_endpoint(
    servers: str = "",
    db: Session = Depends(get_session),
):
    """
    Retrieve risk tier scoring for a batch of servers.
    The `servers` query parameter should be a comma‑separated list of server IDs.
    """
    server_ids = [s.strip() for s in servers.split(",")] if servers else []
    return await get_scoring_batch(server_ids, db)