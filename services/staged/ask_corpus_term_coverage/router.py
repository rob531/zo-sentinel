from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.db import get_session

from .logic import get_corpus_term_coverage


router = APIRouter(prefix="/api")


class ServerTermCoverage(BaseModel):
    server_id: str
    term_count: int
    indexed_at: Optional[datetime] = None
    content_hash: Optional[str] = None


class CorpusTermFrequency(BaseModel):
    term: str
    occurrences: int


class CorpusTermCoverageResponse(BaseModel):
    servers: List[ServerTermCoverage]
    top_terms: List[CorpusTermFrequency]
    total_servers: int
    total_terms: int


@router.get(
    "/ask/corpus-term-coverage",
    response_model=CorpusTermCoverageResponse,
)
def corpus_term_coverage(
    limit: int = Query(default=25),
    session=Depends(get_session),
):
    return get_corpus_term_coverage(session, limit=limit)


if __name__ == "__main__":
    print("PASS")