from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc
from .logic import *


router = APIRouter(prefix="/api")


class DuplicateSnippetGroup(BaseModel):
    content_hash: str
    servers: list[str]
    rows: int
    first_indexed_at: datetime | None = None
    last_indexed_at: datetime | None = None


class DuplicateSnippetResponse(BaseModel):
    total_rows: int
    distinct_hashes: int
    distinct_servers: int
    duplicate_groups: int
    duplicate_rows: int
    groups: list[DuplicateSnippetGroup]


def _normalise_logic_result(result: Any, session: Session) -> dict[str, Any] | None:
    if result is None:
        return None

    if isinstance(result, dict):
        groups = result.get("groups", [])
        payload = {
            "total_rows": int(result.get("total_rows", 0)),
            "distinct_hashes": int(result.get("distinct_hashes", 0)),
            "distinct_servers": int(result.get("distinct_servers", 0)),
            "duplicate_groups": int(result.get("duplicate_groups", len(groups))),
            "duplicate_rows": int(result.get("duplicate_rows", 0)),
            "groups": groups[:20],
        }
        return payload

    if isinstance(result, (list, tuple)):
        groups = list(result)[:20]
        return {
            "total_rows": 0,
            "distinct_hashes": 0,
            "distinct_servers": 0,
            "duplicate_groups": len(groups),
            "duplicate_rows": sum(int(group.get("rows", 0)) for group in groups),
            "groups": groups,
        }

    return None


def _call_logic(session: Session) -> dict[str, Any] | None:
    candidates = (
        "get_duplicate_snippet_report",
        "get_duplicate_snippet_data",
        "get_duplicate_snippet_summary",
        "get_duplicate_snippet_stats",
        "get_duplicate_snippets",
    )
    for name in candidates:
        function = globals().get(name)
        if callable(function):
            return _normalise_logic_result(function(session), session)
    return None


def _read_from_model(session: Session) -> dict[str, Any]:
    statement = select(
        AskCorpusDoc.content_hash,
        AskCorpusDoc.server_id,
        AskCorpusDoc.indexed_at,
    )
    rows = session.execute(statement).all()

    by_hash: dict[str, list[tuple[Any, Any]]] = {}
    for content_hash, server_id, indexed_at in rows:
        by_hash.setdefault(str(content_hash), []).append((server_id, indexed_at))

    all_servers = {
        str(server_id)
        for hash_rows in by_hash.values()
        for server_id, _ in hash_rows
        if server_id is not None
    }

    duplicate_hashes: dict[str, dict[str, Any]] = {}
    for content_hash, hash_rows in by_hash.items():
        servers = sorted(
            {
                str(server_id)
                for server_id, _ in hash_rows
                if server_id is not None
            }
        )
        if len(servers) <= 1:
            continue

        indexed_values = [
            indexed_at
            for _, indexed_at in hash_rows
            if indexed_at is not None
        ]
        duplicate_hashes[content_hash] = {
            "content_hash": content_hash,
            "servers": servers,
            "rows": len(hash_rows),
            "first_indexed_at": min(indexed_values) if indexed_values else None,
            "last_indexed_at": max(indexed_values) if indexed_values else None,
        }

    groups = sorted(
        duplicate_hashes.values(),
        key=lambda group: (-group["rows"], group["content_hash"]),
    )[:20]

    return {
        "total_rows": len(rows),
        "distinct_hashes": len(by_hash),
        "distinct_servers": len(all_servers),
        "duplicate_groups": len(duplicate_hashes),
        "duplicate_rows": sum(group["rows"] for group in duplicate_hashes.values()),
        "groups": groups,
    }


@router.get(
    "/ask-corpus/duplicates",
    response_model=DuplicateSnippetResponse,
    summary="Find duplicate ask-corpus snippets across servers",
)
def get_ask_corpus_duplicates(
    session: Session = Depends(get_session),
) -> DuplicateSnippetResponse:
    payload = _call_logic(session)
    if payload is None:
        payload = _read_from_model(session)
    return DuplicateSnippetResponse(**payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id VARCHAR NOT NULL,
                    snippet TEXT NOT NULL,
                    terms TEXT,
                    content_hash VARCHAR NOT NULL,
                    indexed_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    ('server-a', 'snippet one', 'alpha', 'h1', '2024-01-01 00:00:00'),
                    ('server-b', 'snippet one', 'alpha', 'h1', '2024-01-02 00:00:00'),
                    ('server-c', 'snippet two', 'beta', 'h2', '2024-01-03 00:00:00'),
                    ('server-d', 'snippet three', 'gamma', 'h3', '2024-01-04 00:00:00'),
                    ('server-e', 'snippet four', 'delta', 'h4', '2024-01-05 00:00:00')
                """
            )
        )

    def override_get_session():
        testing_session = TestingSession()
        try:
            yield testing_session
        finally:
            testing_session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask-corpus/duplicates")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 5
    assert payload["distinct_hashes"] == 4
    assert payload["duplicate_groups"] == 1
    assert set(payload["groups"][0]["servers"]) == {"server-a", "server-b"}
    print("PASS")