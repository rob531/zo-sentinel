"""Acceptance self-test for the ask_corpus_snippet_search service."""

from __future__ import annotations

import sys


def run() -> bool:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import AskCorpusDoc, Base

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add_all(
        [
            AskCorpusDoc(
                server_id="s1",
                snippet="Python security guide",
                terms=["security", "python"],
                content_hash="hash-s1",
                indexed_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            ),
            AskCorpusDoc(
                server_id="s2",
                snippet="Rust memory safety",
                terms=["memory", "rust"],
                content_hash="hash-s2",
                indexed_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            ),
            AskCorpusDoc(
                server_id="s3",
                snippet="Go networking",
                terms=["network", "http"],
                content_hash="hash-s3",
                indexed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    response = client.get("/api/ask/snippets", params={"server_id": "s1"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(body["snippets"]), body
    assert body["count"] == 1, body
    assert [item["server_id"] for item in body["snippets"]] == ["s1"], body
    assert body["snippets"][0]["content_hash"] == "hash-s1", body
    assert body["snippets"][0]["terms"] == ["security", "python"], body

    response = client.get("/api/ask/snippets", params={"term": "SECUR"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(body["snippets"]), body
    assert [item["server_id"] for item in body["snippets"]] == ["s1"], body

    response = client.get(
        "/api/ask/snippets",
        params={"server_id": "s1", "term": "rust", "limit": 100},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(body["snippets"]), body
    assert body["count"] == 0, body

    response = client.get("/api/ask/snippets", params={"limit": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == len(body["snippets"]), body
    assert body["count"] == 2, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)