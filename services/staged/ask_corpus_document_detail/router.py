"""router.py -- the service's HTTP surface (one concern, one file).

Thin wrapper that injects the real Session via Depends(get_session) and
delegates to the service's logic module.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

# The logic function that performs the DB query and assembles the response.
# It must accept a Session and a server_id and return a dict matching the
# CorpusResponse schema defined below.
from .logic import get_corpus_document_detail

router = APIRouter(prefix="/api/ask/corpus", tags=["ask_corpus_document_detail"])


class Document(BaseModel):
    server_id: str
    snippet: str | None = None
    terms: list[str] | None = None
    content_hash: str | None = None
    indexed_at: str | None = None


class CorpusResponse(BaseModel):
    server_id: str
    document_count: int
    total_terms: int
    latest_indexed_at: str | None = None
    content_hash: str | None = None
    documents: list[Document]


@router.get("/{server_id}", response_model=CorpusResponse)
def get_corpus_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> CorpusResponse:
    """Return indexed corpus snippets for a given server."""
    payload = get_corpus_document_detail(db, server_id)
    return CorpusResponse(**payload)