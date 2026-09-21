from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import (
    get_perspective_lifecycle_summary,
    PerspectiveLifecycleSummaryResponse,
)

router = APIRouter(prefix="/api")


@router.get(
    "/perspectives/summary",
    response_model=PerspectiveLifecycleSummaryResponse,
)
def perspective_summary(session: Session = Depends(get_session)):
    return get_perspective_lifecycle_summary(session)