"""Acceptance self-test for ask_corpus_growth_trend."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
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

    now = datetime.now().replace(microsecond=0)
    first_day = now - timedelta(days=1)
    second_day = now - timedelta(days=2)

    session = TestingSession()
    try:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="srv-growth-1",
                    content_hash="growth-hash-1",
                    indexed_at=first_day,
                    snippet="first corpus document",
                    terms="corpus growth",
                ),
                AskCorpusDoc(
                    server_id="srv-growth-2",
                    content_hash="growth-hash-2",
                    indexed_at=first_day,
                    snippet="second corpus document",
                    terms="corpus growth",
                ),
                AskCorpusDoc(
                    server_id="srv-growth-3",
                    content_hash="growth-hash-3",
                    indexed_at=second_day,
                    snippet="third corpus document",
                    terms="corpus growth",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

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
    response = client.get("/api/ask/corpus-growth", params={"days": 30})
    assert response.status_code == 200, response.text

    body = response.json()
    series = body["series"]
    assert len(series) == 2, body
    assert sum(item["docs_indexed"] for item in series) == 3, body
    assert any(item["docs_indexed"] == 2 for item in series), body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)