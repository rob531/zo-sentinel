from __future__ import annotations

import inspect
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc
from .logic import get_corpus_terms_cardinality as compute_corpus_terms_cardinality

router = APIRouter(prefix="/api")


class CorpusTerm(BaseModel):
    term: str
    count: int


class CorpusTermsCardinalityResponse(BaseModel):
    doc_count: int
    distinct_server_count: int
    min_terms: int
    max_terms: int
    mean_terms: float
    median_terms: float
    p90_terms: float
    empty_terms_docs: int
    top_terms: List[CorpusTerm]


@router.get(
    "/ask/corpus-terms-cardinality",
    response_model=CorpusTermsCardinalityResponse,
)
async def ask_corpus_terms_cardinality(
    session: Session = Depends(get_session),
) -> CorpusTermsCardinalityResponse:
    payload = compute_corpus_terms_cardinality(session)
    if inspect.isawaitable(payload):
        payload = await payload
    return CorpusTermsCardinalityResponse(**payload)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id TEXT NOT NULL,
                    snippet TEXT,
                    terms TEXT,
                    content_hash TEXT,
                    indexed_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    ('server-0', 'zero', '[]', 'hash-0', '2024-01-01'),
                    ('server-1', 'one', '["alpha"]', 'hash-1', '2024-01-02'),
                    ('server-2', 'two', '["alpha", "beta"]', 'hash-2', '2024-01-03'),
                    ('server-3', 'three', '["alpha", "beta", "gamma"]', 'hash-3', '2024-01-04')
                """
            )
        )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/ask/corpus-terms-cardinality")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["doc_count"] == 4
        assert payload["min_terms"] == 0
        assert payload["max_terms"] == 3
        assert 1.0 <= payload["median_terms"] <= 2.0
        assert isinstance(payload["top_terms"], list)

    print("PASS")