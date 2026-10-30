from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry

from .logic import get_shift_ledger

router = APIRouter()


@router.get("/api/disputes/shift-ledger")
def shift_ledger(
    status: str | None = Query(default=None),
    server_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
):
    return get_shift_ledger(
        session=session,
        status=status,
        server_id=server_id,
        limit=limit,
    )