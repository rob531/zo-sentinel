from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import AskCorpusDoc

try:
    from .logic import get_duplicate_content_hashes
except ImportError:
    get_duplicate_content_hashes = None


router = APIRouter(prefix="/api")


class DuplicateContentHashGroup(BaseModel):
    content_hash: str
    doc_count: int
    distinct_servers: int
    first_indexed_at: str | None = None
    last_indexed_at: str | None = None
    server_ids: list[str] = Field(default_factory=list)


class DuplicateContentHashesResponse(BaseModel):
    min_group_size: int
    groups: list[DuplicateContentHashGroup]
    total_groups: int
    truncated: bool


def _terms_length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return 0
        if isinstance(decoded, (list, tuple, set)):
            return len(decoded)
    return 0


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _find_duplicate_content_hashes(
    session: Any,
    limit: int,
    min_group_size: int,
) -> dict[str, Any]:
    rows = session.query(
        AskCorpusDoc.content_hash,
        AskCorpusDoc.server_id,
        AskCorpusDoc.indexed_at,
        AskCorpusDoc.terms,
    ).all()

    grouped: dict[str, dict[str, Any]] = {}

    for content_hash, server_id, indexed_at, terms in rows:
        hash_key = "" if content_hash is None else str(content_hash)
        server_key = "" if server_id is None else str(server_id)
        timestamp = _timestamp(indexed_at)
        term_count = _terms_length(terms)

        group = grouped.setdefault(
            hash_key,
            {
                "content_hash": hash_key,
                "doc_count": 0,
                "servers": set(),
                "first_indexed_at": None,
                "last_indexed_at": None,
                "term_count": 0,
            },
        )
        group["doc_count"] += 1
        group["servers"].add(server_key)
        group["term_count"] += term_count

        if timestamp is not None:
            if group["first_indexed_at"] is None or timestamp < group["first_indexed_at"]:
                group["first_indexed_at"] = timestamp
            if group["last_indexed_at"] is None or timestamp > group["last_indexed_at"]:
                group["last_indexed_at"] = timestamp

    qualifying = [
        group
        for group in grouped.values()
        if group["doc_count"] >= min_group_size
    ]
    qualifying.sort(key=lambda group: (-group["doc_count"], group["content_hash"]))

    total_groups = len(qualifying)
    selected = qualifying[:limit]

    return {
        "min_group_size": min_group_size,
        "groups": [
            {
                "content_hash": group["content_hash"],
                "doc_count": group["doc_count"],
                "distinct_servers": len(group["servers"]),
                "first_indexed_at": group["first_indexed_at"],
                "last_indexed_at": group["last_indexed_at"],
                "server_ids": sorted(group["servers"]),
            }
            for group in selected
        ],
        "total_groups": total_groups,
        "truncated": total_groups > len(selected),
    }


@router.get(
    "/ask-corpus/duplicate-content-hashes",
    response_model=DuplicateContentHashesResponse,
)
def list_duplicate_content_hashes(
    limit: int = Query(default=50),
    min_group_size: int = Query(default=2),
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    clamped_limit = max(1, min(500, int(limit)))
    effective_min_group_size = max(1, int(min_group_size))
    return _find_duplicate_content_hashes(
        session,
        limit=clamped_limit,
        min_group_size=effective_min_group_size,
    )


def _self_test() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE ask_corpus_index (
            content_hash TEXT,
            server_id TEXT,
            indexed_at TEXT,
            terms TEXT
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO ask_corpus_index
            (content_hash, server_id, indexed_at, terms)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("h1", "s1", "2024-01-01T00:00:00", '["a"]'),
            ("h1", "s2", "2024-01-02T00:00:00", '["b", "c"]'),
            ("h1", "s1", "2024-01-03T00:00:00", '["d"]'),
            ("h2", "s3", "2024-01-04T00:00:00", '["e"]'),
            ("h2", "s4", "2024-01-05T00:00:00", '["f"]'),
        ],
    )

    class _Query:
        def all(self) -> list[tuple[Any, ...]]:
            return connection.execute(
                """
                SELECT content_hash, server_id, indexed_at, terms
                FROM ask_corpus_index
                """
            ).fetchall()

    class _Session:
        def query(self, *columns: Any) -> _Query:
            return _Query()

    test_app = FastAPI()
    test_app.include_router(router)
    fake_session = _Session()
    test_app.dependency_overrides[get_session] = lambda: fake_session

    result = list_duplicate_content_hashes(
        limit=50,
        min_group_size=2,
        session=fake_session,
    )

    assert result["min_group_size"] == 2
    assert len(result["groups"]) == 2
    h1 = next(group for group in result["groups"] if group["content_hash"] == "h1")
    assert h1["doc_count"] == 3
    assert h1["distinct_servers"] == 2
    assert all(
        isinstance(group["first_indexed_at"], str)
        and isinstance(group["last_indexed_at"], str)
        for group in result["groups"]
    )

    print("PASS")


if __name__ == "__main__":
    _self_test()