from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_failure_taxonomy

router = APIRouter(prefix="/api/cadence", tags=["cadence"])


@router.get("/failures")
def get_cadence_failures(
    days: int = 30,
    job: str | None = None,
    session: Session = Depends(get_session),
):
    return get_failure_taxonomy(days=days, job=job, session=session)