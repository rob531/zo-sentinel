from typing import Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_verdict_breakdown_by_source

router = APIRouter(prefix="/api", tags=["verdict_breakdown_by_source"])


@router.get(
    "/verdicts/breakdown/source",
    response_model=Dict[str, int],
    summary="Breakdown of verdicts by registry source",
)
def verdict_breakdown_by_source(session: Session = Depends(get_session)) -> Dict[str, int]:
    """
    Return a mapping of registry source names to the count of servers
    associated with each source.
    """
    return get_verdict_breakdown_by_source(session)