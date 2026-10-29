from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_adapter_sha_coverage

router = APIRouter(prefix="/api")


@router.get("/axis/adapter-sha-coverage")
def axis_adapter_sha_coverage(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
):
    return get_axis_adapter_sha_coverage(session, days)