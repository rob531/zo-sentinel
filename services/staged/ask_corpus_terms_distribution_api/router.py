"""Thin HTTP surface for corpus term-distribution statistics."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc

from .logic import get_terms_distribution

router = APIRouter(tags=["ask-corpus"])


class TermCount(BaseModel):
    term: str
    doc_count: int


class TermsDistributionResponse(BaseModel):
    terms: list[TermCount]
    total_docs: int
    unique_terms: int
    top_n: int


@router.get(
    "/ask/corpus/terms-distribution",
    response_model=TermsDistributionResponse,
)
def ask_corpus_terms_distribution(
    top_n: int = 50,
    db: Session = Depends(get_session),
) -> TermsDistributionResponse:
    return TermsDistributionResponse(**get_terms_distribution(db, top_n))


if __name__ == "__main__":
    from collections.abc import Generator
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(bind=engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    indexed_at = datetime.now(timezone.utc)
    with session_factory() as session:
        session.execute(
            insert(AskCorpusDoc.__table__).values(
                [
                    {
                        "server_id": "contract-server-1",
                        "snippet": "Authorization policy example",
                        "terms": ["authz", "network"],
                        "content_hash": "contract-hash-1",
                        "indexed_at": indexed_at,
                    },
                    {
                        "server_id": "contract-server-2",
                        "snippet": "Another authorization policy",
                        "terms": ["authz"],
                        "content_hash": "contract-hash-2",
                        "indexed_at": indexed_at,
                    },
                    {
                        "server_id": "contract-server-3",
                        "snippet": "Document without extracted terms",
                        "terms": None,
                        "content_hash": "contract-hash-3",
                        "indexed_at": indexed_at,
                    },
                ]
            )
        )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/ask/corpus/terms-distribution")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_docs"] == 3, payload
    assert payload["unique_terms"] == 2, payload
    assert payload["top_n"] == 50, payload
    term_counts = {item["term"]: item["doc_count"] for item in payload["terms"]}
    assert term_counts.get("authz") == 2, payload

    print("PASS")