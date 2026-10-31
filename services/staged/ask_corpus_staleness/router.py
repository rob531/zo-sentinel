from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_corpus_staleness

router = APIRouter(prefix="/api", tags=["ask"])


class CorpusStalenessBuckets(BaseModel):
    fresh: int = 0
    aging: int = 0
    stale: int = 0
    never: int = 0


class CorpusStalenessServer(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    doc_count: int
    last_indexed_at: Optional[datetime] = None
    age_days: int
    bucket: str


class CorpusStalenessResponse(BaseModel):
    total_unstale: int
    buckets: CorpusStalenessBuckets
    servers: List[CorpusStalenessServer]


@router.get(
    "/ask/corpus-staleness",
    response_model=CorpusStalenessResponse,
    summary="Report corpus indexing staleness by server",
)
def ask_corpus_staleness(
    session: Session = Depends(get_session),
) -> CorpusStalenessResponse:
    return get_corpus_staleness(session)