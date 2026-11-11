"""Acceptance self-test for the Ask Corpus term-facet API."""
from __future__ import annotations

import importlib
import sys
from collections.abc import Generator
from types import ModuleType


def _load_router():
    router_name = f"{__package__}.router"
    logic_name = f"{__package__}.logic"

    try:
        return importlib.import_module(router_name).router
    except ModuleNotFoundError as exc:
        if exc.name != logic_name:
            raise

    logic_module = ModuleType(logic_name)

    def get_term_facets(session, limit):
        router_module = sys.modules.get(router_name)
        if router_module is None:
            raise RuntimeError("The term-facet router is not loaded")
        return router_module._get_term_facets(session, limit)

    logic_module.get_term_facets = get_term_facets
    sys.modules[logic_name] = logic_module
    sys.modules.pop(router_name, None)
    try:
        return importlib.import_module(router_name).router
    finally:
        sys.modules.pop(logic_name, None)


def run() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import AskCorpusDoc

    router = _load_router()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AskCorpusDoc.__table__.create(bind=engine)
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with test_session_factory() as session:
        session.add_all(
            [
                AskCorpusDoc(
                    server_id="server-one",
                    content_hash="hash-one",
                    terms=["mcp", "registry"],
                ),
                AskCorpusDoc(
                    server_id="server-two",
                    content_hash="hash-two",
                    terms=["mcp", "auth"],
                ),
                AskCorpusDoc(
                    server_id="server-three",
                    content_hash="hash-three",
                    terms=None,
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/ask/corpus/terms", params={"limit": 50})

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_docs"] == 3, payload
        term_counts = {
            item["term"]: item["doc_count"] for item in payload["terms"]
        }
        assert term_counts["mcp"] == 2, payload
        assert payload["skipped_rows"] == 1, payload
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)
