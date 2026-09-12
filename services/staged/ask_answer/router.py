from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_ask_answer

router = APIRouter(prefix="/api", tags=["ask_answer"])


@router.get("/ask")
def ask_endpoint(
    q: str = Query(..., alias="q"),
    limit: int = Query(10, alias="limit"),
    session: Session = Depends(get_session),
):
    return get_ask_answer(q, limit, session)