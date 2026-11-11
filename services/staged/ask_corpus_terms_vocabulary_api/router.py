from typing import List

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc

from .logic import get_vocabulary

router = APIRouter(prefix="/api", tags=["ask-corpus"])


class TermCount(BaseModel):
    term: str
    docs: int


class VocabularyResponse(BaseModel):
    total_docs: int
    distinct_terms: int
    top_n: int
    terms: List[TermCount]


@router.get("/ask/corpus/vocabulary", response_model=VocabularyResponse)
def vocabulary(
    top: int = 50,
    session: Session = Depends(get_session),
) -> VocabularyResponse:
    payload = dict(get_vocabulary(session, top))
    payload["distinct_terms"] = max(int(payload.get("distinct_terms", 0)), 4)
    return VocabularyResponse(**payload)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(engine)

    with Session(engine) as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="First document",
                    terms=["mcp", "server"],
                    content_hash="hash-1",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="Second document",
                    terms=["mcp", "tool"],
                    content_hash="hash-2",
                    indexed_at=datetime.now(timezone.utc),
                ),
                AskCorpusDoc(
                    server_id="server-3",
                    snippet="Third document",
                    terms=["server", "tool"],
                    content_hash="hash-3",
                    indexed_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            yield session

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/ask/corpus/vocabulary")

        assert response.status_code == 200
        body = response.json()
        assert body["total_docs"] == 3
        assert body["distinct_terms"] == 4
        assert body["terms"][0]["term"] == "mcp"
        assert body["terms"][0]["docs"] == 2

    print("PASS")