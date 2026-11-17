from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_index_timeline


class IndexTimelinePoint(BaseModel):
    date: date
    doc_count: int
    server_count: int


class IndexTimelineResponse(BaseModel):
    days: int
    series: List[IndexTimelinePoint]
    total_docs: int
    first_indexed_at: Optional[datetime] = None
    last_indexed_at: Optional[datetime] = None


router = APIRouter(prefix="/api", tags=["ask-corpus"])


@router.get(
    "/ask-corpus/index-timeline",
    response_model=IndexTimelineResponse,
)
def ask_corpus_index_timeline(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> IndexTimelineResponse:
    payload = get_index_timeline(db, days)
    return IndexTimelineResponse.model_validate(payload)