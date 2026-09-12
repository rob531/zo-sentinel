from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_perspective_snapshots

router = APIRouter(prefix="/api")


@router.get("/perspectives/{perspective_id}/snapshots")
def read_perspective_snapshots(
    perspective_id: str,
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    return get_perspective_snapshots(
        perspective_id=perspective_id,
        limit=limit,
        offset=offset,
        session=session,
    )