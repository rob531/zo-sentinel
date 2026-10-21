from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_session
from .logic import get_attestation_coverage

router = APIRouter(prefix="/api")


@router.get("/attestations/coverage")
def attestation_coverage_endpoint(
    days: int = Query(90, ge=1),
    status: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    """Return attestation coverage summary."""
    return get_attestation_coverage(session=session, days=days, status=status)