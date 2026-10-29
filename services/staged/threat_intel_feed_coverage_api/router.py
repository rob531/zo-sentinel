from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_feed_coverage

router = APIRouter(prefix="/api")


@router.get("/threat-intel/feed-coverage")
def threat_intel_feed_coverage(session: Session = Depends(get_session)):
    return get_feed_coverage(session)