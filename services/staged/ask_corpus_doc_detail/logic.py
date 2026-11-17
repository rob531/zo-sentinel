"""logic.py -- corpus document detail data/computation layer."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


def _parse_terms(value: Any) -> list[Any]:
    """Return the stored terms as a list, decoding JSON when necessary."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def get_corpus_doc_detail(db: Session, server_id: str) -> dict[str, Any] | None:
    """Load the indexed corpus document for a server from the real app store."""
    row = db.execute(
        select(AskCorpusDoc)
        .where(AskCorpusDoc.server_id == server_id)
        .limit(1)
    ).scalar_one_or_none()

    if row is None:
        return None

    return {
        "server_id": row.server_id,
        "snippet": row.snippet,
        "terms": _parse_terms(row.terms),
        "content_hash": row.content_hash,
        "indexed_at": row.indexed_at,
    }


# Keep the common service-function spellings available to thin routers.
get_corpus_doc = get_corpus_doc_detail
get_doc = get_corpus_doc_detail
get_document = get_corpus_doc_detail


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with TestingSessionLocal() as db:
        db.add_all(
            [
                AskCorpusDoc(
                    server_id="srv-1",
                    snippet="First server corpus snippet",
                    terms=["alpha", "beta", "gamma"],
                    content_hash="hash-srv-1",
                    indexed_at=datetime(2024, 1, 1, 12, 0, 0),
                ),
                AskCorpusDoc(
                    server_id="srv-2",
                    snippet="Second server corpus snippet",
                    terms=["delta", "epsilon", "zeta"],
                    content_hash="hash-srv-2",
                    indexed_at=datetime(2024, 1, 2, 12, 0, 0),
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with TestingSessionLocal() as db:
            yield db

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/ask/corpus/{server_id}/doc")
    def corpus_doc_detail(server_id: str, db: Session = Depends(get_session)):
        result = get_corpus_doc_detail(db, server_id)
        if result is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Corpus document not found")
        return result

    response = TestClient(test_app).get("/api/ask/corpus/srv-1/doc")
    assert response.status_code == 200
    payload = response.json()
    assert payload["server_id"] == "srv-1"
    assert len(payload["terms"]) == 3
    assert payload["content_hash"] == "hash-srv-1"
    print("PASS")