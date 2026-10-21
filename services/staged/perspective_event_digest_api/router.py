from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Perspective, PerspectiveEvent
from .logic import (
    PerspectiveDigest,
    PerspectiveDigestResponse,
    get_perspective_event_digest_logic,
)

router = APIRouter(prefix="/api", tags=["perspectives"])


@router.get(
    "/perspectives/events/digest",
    response_model=PerspectiveDigestResponse,
)
def get_perspective_event_digest(
    days: int = 30,
    session: Session = Depends(get_session),
) -> PerspectiveDigestResponse:
    return get_perspective_event_digest_logic(days=days, session=session)