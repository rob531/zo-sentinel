"""HTTP router for the staged ask-corpus document detail service."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc

from .logic import get_corpus_doc

router = APIRouter(prefix="/api/ask/corpus", tags=["ask-corpus"])


class CorpusDocResponse(BaseModel):
    server_id: str
    snippet: str
    terms: list[str]
    content_hash: str
    indexed_at: datetime


@router.get("/{server_id}/doc", response_model=CorpusDocResponse)
def get_corpus_doc_detail(
    server_id: str, db: Session = Depends(get_session)
) -> CorpusDocResponse:
    document = get_corpus_doc(db, server_id)

    if isinstance(document, dict):
        snippet = document["snippet"]
        terms = document["terms"]
        content_hash = document["content_hash"]
        indexed_at = document["indexed_at"]
    else:
        snippet, terms, content_hash, indexed_at = document

    return CorpusDocResponse(
        server_id=server_id,
        snippet=snippet,
        terms=terms,
        content_hash=content_hash,
        indexed_at=indexed_at,
    )


if __name__ == "__main__":
    from datetime import timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="srv-1",
                    snippet="first server snippet",
                    terms=["alpha", "beta", "gamma"],
                    content_hash="hash-srv-1",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="srv-2",
                    snippet="second server snippet",
                    terms=["delta", "epsilon", "zeta"],
                    content_hash="hash-srv-2",
                    indexed_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/corpus/srv-1/doc")
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    assert len(payload["terms"]) == 3
    assert payload["content_hash"] == "hash-srv-1"

    print("PASS")