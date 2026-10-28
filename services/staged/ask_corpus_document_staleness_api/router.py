from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_corpus_staleness_report

router = APIRouter(prefix="/api", tags=["ask-corpus"])


@router.get("/ask/corpus/staleness")
def ask_corpus_document_staleness(
    days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_corpus_staleness_report(session, days)