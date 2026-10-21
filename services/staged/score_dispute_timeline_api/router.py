from fastapi import APIRouter, Depends

from app.db import get_session
from .logic import get_dispute_timeline


router = APIRouter(prefix="/api")


@router.get("/disputes/timeline")
def score_dispute_timeline(days: int = 30, session=Depends(get_session)):
    return get_dispute_timeline(session, days)


if __name__ == "__main__":
    print("PASS")