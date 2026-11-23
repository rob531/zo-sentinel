"""logic.py -- corpus term coverage computation and endpoint."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


class TermCoverageItem(BaseModel):
    term: str
    documents: int


class TermCoverageResponse(BaseModel):
    document_count: int
    empty_terms_documents: int
    distinct_terms: int
    terms: List[TermCoverageItem]


router = APIRouter(prefix="/api")


@router.get(
    "/corpus/term-coverage",
    response_model=TermCoverageResponse,
)
def corpus_term_coverage(
    db: Session = Depends(get_session),
    limit: int = Query(50, ge=0),
    server_id: Optional[str] = Query(None),
) -> TermCoverageResponse:
    """Summarise term coverage across the searchable ask corpus."""
    statement = select(AskCorpusDoc.server_id, AskCorpusDoc.terms)
    if server_id is not None:
        statement = statement.where(AskCorpusDoc.server_id == server_id)

    rows = db.execute(statement).all()
    document_count = len(rows)
    empty_terms_documents = 0
    term_documents: Counter[str] = Counter()

    for _, terms in rows:
        if not terms:
            empty_terms_documents += 1
            continue

        for term in terms:
            term_documents[str(term)] += 1

    ordered_terms = sorted(
        term_documents.items(),
        key=lambda item: (-item[1], item[0]),
    )[:limit]

    return TermCoverageResponse(
        document_count=document_count,
        empty_terms_documents=empty_terms_documents,
        distinct_terms=len(term_documents),
        terms=[
            TermCoverageItem(term=term, documents=documents)
            for term, documents in ordered_terms
        ],
    )


# Public aliases retained for callers that use the service's computation directly.
term_coverage = corpus_term_coverage
term_coverage_summary = corpus_term_coverage
get_term_coverage = corpus_term_coverage


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(bind=engine)
    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="first",
                    terms=["mcp", "oauth"],
                    content_hash="hash-1",
                    indexed_at=datetime.utcnow(),
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="second",
                    terms=["mcp"],
                    content_hash="hash-2",
                    indexed_at=datetime.utcnow(),
                ),
                AskCorpusDoc(
                    server_id="server-3",
                    snippet="third",
                    terms=[],
                    content_hash="hash-3",
                    indexed_at=datetime.utcnow(),
                ),
            ]
        )
        session.commit()

        def override_get_session():
            with TestingSession() as session:
                yield session

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        response = TestClient(test_app).get("/api/corpus/term-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["document_count"] == 3
        assert payload["empty_terms_documents"] == 1
        assert payload["terms"][0] == {"term": "mcp", "documents": 2}

    print("PASS")