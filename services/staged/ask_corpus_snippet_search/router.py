"""router.py -- the service's HTTP surface for corpus snippet search."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import search_snippets

router = APIRouter(prefix="/api", tags=["ask-corpus-snippet-search"])


class SnippetResponse(BaseModel):
    server_id: str | None
    snippet: str | None
    terms: list[str]
    content_hash: str | None
    indexed_at: datetime | None


class SnippetSearchResponse(BaseModel):
    server_id: str | None
    term: str | None
    limit: int
    count: int
    snippets: list[SnippetResponse]


def _snippet_value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


@router.get("/ask/snippets", response_model=SnippetSearchResponse)
def get_snippets(
    server_id: str | None = Query(default=None),
    term: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_session),
) -> SnippetSearchResponse:
    rows = search_snippets(db, server_id=server_id, term=term, limit=limit)
    snippets = [
        SnippetResponse(
            server_id=_snippet_value(row, "server_id"),
            snippet=_snippet_value(row, "snippet"),
            terms=list(_snippet_value(row, "terms", []) or []),
            content_hash=_snippet_value(row, "content_hash"),
            indexed_at=_snippet_value(row, "indexed_at"),
        )
        for row in rows
    ]
    return SnippetSearchResponse(
        server_id=server_id,
        term=term,
        limit=limit,
        count=len(snippets),
        snippets=snippets,
    )


def _run_self_test() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import AskCorpusDoc

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    AskCorpusDoc.__table__.create(bind=engine)
    session = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            AskCorpusDoc(
                server_id="s1",
                snippet="first snippet",
                terms=["alpha", "one"],
                content_hash="hash-1",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="s2",
                snippet="second snippet",
                terms=["beta", "two"],
                content_hash="hash-2",
                indexed_at=now,
            ),
            AskCorpusDoc(
                server_id="s3",
                snippet="third snippet",
                terms=["gamma", "three"],
                content_hash="hash-3",
                indexed_at=now,
            ),
        ]
    )
    session.commit()
    session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db_session = TestingSessionLocal()
        try:
            yield db_session
        finally:
            db_session.close()

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get("/api/ask/snippets", params={"server_id": "s1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == len(payload["snippets"]) == 1
    assert payload["snippets"][0]["server_id"] == "s1"

    response = client.get("/api/ask/snippets", params={"term": "BETA"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == len(payload["snippets"]) == 1
    assert payload["snippets"][0]["server_id"] == "s2"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()