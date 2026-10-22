from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry
from .logic import get_stale_corpus_documents

router = APIRouter(prefix="/api/ask/corpus", tags=["ask-corpus"])


class StaleDocumentResponse(BaseModel):
    server_id: str
    name: str | None = None
    indexed_at: str
    age_days: int
    content_hash: str | None = None
    reason: str


class StaleCorpusResponse(BaseModel):
    max_age_days: int
    stale_count: int
    orphaned_count: int
    documents: list[StaleDocumentResponse]


@router.get("/stale", response_model=StaleCorpusResponse)
def stale_corpus_documents(
    max_age_days: int = Query(30, ge=0),
    session: Session = Depends(get_session),
) -> StaleCorpusResponse:
    result = get_stale_corpus_documents(session, max_age_days=max_age_days)
    return StaleCorpusResponse(
        max_age_days=result["max_age_days"],
        stale_count=result["stale_count"],
        orphaned_count=result["orphaned_count"],
        documents=[StaleDocumentResponse(**document) for document in result["documents"]],
    )