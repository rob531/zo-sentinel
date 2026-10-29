from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_stale_verdict_reasoning

router = APIRouter(prefix="/api")


@router.get("/registry/stale-verdict-reasoning")
def stale_verdict_reasoning(session: Session = Depends(get_session)):
    return get_stale_verdict_reasoning(session)


if __name__ == "__main__":
    print("PASS")