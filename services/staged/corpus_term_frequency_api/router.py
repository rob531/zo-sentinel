from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

if __package__:
    from .logic import get_corpus_term_frequency
else:
    from services.staged.corpus_term_frequency_api.logic import (
        get_corpus_term_frequency,
    )


class CorpusTerm(BaseModel):
    term: str
    count: int
    doc_count: int


class CorpusTermFrequencyResponse(BaseModel):
    documents: int
    distinct_terms: int
    avg_terms_per_doc: float
    empty_term_docs: int
    top_terms: List[CorpusTerm]


router = APIRouter(prefix="/api")


@router.get(
    "/ask/corpus/terms",
    response_model=CorpusTermFrequencyResponse,
)
def corpus_term_frequency(
    limit: int = Query(default=20, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> CorpusTermFrequencyResponse:
    return get_corpus_term_frequency(session, limit)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import AskCorpusDoc

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(test_engine)
    test_session_factory = sessionmaker(bind=test_engine)

    with test_session_factory() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="MCP authentication",
                    terms=["mcp", "auth"],
                    content_hash="hash-1",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="MCP shell",
                    terms=["mcp", "shell"],
                    content_hash="hash-2",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-3",
                    snippet="No terms",
                    terms=[],
                    content_hash="hash-3",
                    indexed_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/ask/corpus/terms")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["documents"] == 3
        assert payload["empty_term_docs"] == 1
        assert any(
            term["term"] == "mcp"
            and term["count"] == 2
            and term["doc_count"] == 2
            for term in payload["top_terms"]
        )

        limited_response = client.get("/api/ask/corpus/terms?limit=1")
        assert limited_response.status_code == 200, limited_response.text
        assert len(limited_response.json()["top_terms"]) == 1

    test_engine.dispose()
    print("PASS")