from __future__ import annotations

import inspect
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .logic import *  # noqa: F401,F403
from app.db import get_session
from app.models import AskCorpusDoc


router = APIRouter(prefix="/api")


def _logic_report_function():
    preferred_names = (
        "get_corpus_duplicate_report",
        "get_duplicate_corpus_report",
        "build_corpus_duplicate_report",
        "get_corpus_duplicate_docs",
        "get_duplicate_corpus_docs",
        "fetch_corpus_duplicate_docs",
        "fetch_duplicate_corpus_docs",
        "get_duplicate_docs",
        "fetch_duplicate_docs",
        "analyze_corpus_duplicates",
    )
    namespace = globals()
    for name in preferred_names:
        candidate = namespace.get(name)
        if callable(candidate):
            return candidate

    candidates = [
        value
        for name, value in namespace.items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", "").endswith(".logic")
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError("The corpus duplicate logic module does not expose a report function")


_report_corpus_duplicates = _logic_report_function()


@router.get("/ask/corpus-duplicates")
async def ask_corpus_duplicate_docs(session: Session = Depends(get_session)):
    report = _report_corpus_duplicates(session)
    if inspect.isawaitable(report):
        report = await report
    return report


if __name__ == "__main__":
    from fastapi import FastAPI
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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    testing_session.add_all(
        [
            AskCorpusDoc(
                server_id="server-a",
                snippet="first document",
                terms="alpha",
                content_hash="duplicate-hash",
                indexed_at=datetime.now(timezone.utc),
            ),
            AskCorpusDoc(
                server_id="server-b",
                snippet="second document",
                terms="beta",
                content_hash="duplicate-hash",
                indexed_at=datetime.now(timezone.utc),
            ),
            AskCorpusDoc(
                server_id="server-c",
                snippet="third document",
                terms="gamma",
                content_hash="",
                indexed_at=datetime.now(timezone.utc),
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask/corpus-duplicates")
    response.raise_for_status()
    payload = response.json()

    assert payload["total_docs"] == 3
    assert payload["duplicate_doc_count"] == 2
    assert len(payload["duplicate_groups"]) == 1
    assert payload["duplicate_groups"][0]["doc_count"] == 2
    assert payload["duplicate_groups"][0]["server_ids"] == ["server-a", "server-b"]
    assert payload["servers_without_hash"] == 1

    print("PASS")