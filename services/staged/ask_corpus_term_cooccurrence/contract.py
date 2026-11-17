"""Acceptance self-test for the ask corpus term co-occurrence service."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import AskCorpusDoc

    from .router import router

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    s = TestingSession()
    try:
        s.add_all(
            [
                AskCorpusDoc(
                    server_id="srv1",
                    snippet="MCP filesystem example",
                    terms=["mcp", "filesystem"],
                    content_hash="hash-1",
                ),
                AskCorpusDoc(
                    server_id="srv2",
                    snippet="MCP network example",
                    terms=["mcp", "network"],
                    content_hash="hash-2",
                ),
                AskCorpusDoc(
                    server_id="srv3",
                    snippet="Database example",
                    terms=["database"],
                    content_hash="hash-3",
                ),
            ]
        )
        s.commit()
    finally:
        s.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    response = client.get("/api/ask/corpus/cooccurrence", params={"term": "mcp"})
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["term"] == "mcp", body
    assert body["total_snippets"] == 2, body

    related = {item["term"]: item for item in body["related"]}
    assert "filesystem" in related, body
    assert "network" in related, body
    assert related["filesystem"]["count"] == 1, body
    assert related["network"]["count"] == 1, body
    assert 0.5 <= related["filesystem"]["jaccard"] <= 1.0, body
    assert 0.5 <= related["network"]["jaccard"] <= 1.0, body

    missing = client.get(
        "/api/ask/corpus/cooccurrence",
        params={"term": "term-with-no-matches"},
    )
    assert missing.status_code == 404, missing.text
    assert missing.json() == {"error": "term_not_found"}, missing.text
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- fail loudly
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)