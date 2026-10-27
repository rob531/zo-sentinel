from typing import List

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc

from .logic import get_term_overlap

router = APIRouter()


class TopServer(BaseModel):
    server_id: str
    count: int


class TermOverlap(BaseModel):
    term: str
    doc_count: int
    server_count: int
    top_servers: List[TopServer]


class OverlapPair(BaseModel):
    term_a: str
    term_b: str
    shared_servers: List[str]


class TermOverlapResponse(BaseModel):
    total_docs: int
    terms: List[TermOverlap]
    overlap_pairs: List[OverlapPair]


@router.get(
    "/ask-corpus/term-overlap",
    response_model=TermOverlapResponse,
)
def ask_corpus_term_overlap(
    top: int = Query(20, ge=1),
    session=Depends(get_session),
) -> TermOverlapResponse:
    return get_term_overlap(session, top)


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    db = TestSession()
    try:
        now = datetime.utcnow()
        db.add_all(
            [
                AskCorpusDoc(
                    server_id="server-a",
                    snippet="alpha and shared terms",
                    terms=["alpha", "shared"],
                    content_hash="contract-hash-a",
                    indexed_at=now,
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    snippet="shared and beta terms",
                    terms=["shared", "beta"],
                    content_hash="contract-hash-b",
                    indexed_at=now,
                ),
                AskCorpusDoc(
                    server_id="server-c",
                    snippet="alpha terms",
                    terms=["alpha"],
                    content_hash="contract-hash-c",
                    indexed_at=now,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    with TestClient(test_app) as client:
        response = client.get("/api/ask-corpus/term-overlap", params={"top": 20})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_docs"] == 3
        assert payload["terms"]
        assert all(term["doc_count"] >= 1 for term in payload["terms"])
        assert any(
            pair["term_a"] == "alpha"
            and pair["term_b"] == "shared"
            and pair["shared_servers"] == ["server-a"]
            for pair in payload["overlap_pairs"]
        )

    print("PASS")