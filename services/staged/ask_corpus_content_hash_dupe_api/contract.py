from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc


class DuplicateHash(BaseModel):
    content_hash: str
    server_ids: list[Any]
    doc_count: int


class ContentHashDuplicatesResponse(BaseModel):
    duplicate_hashes: list[DuplicateHash]
    total_duplicate_hashes: int
    docs_scanned: int
    distinct_servers: int


def create_app() -> FastAPI:
    api = FastAPI()

    @api.get(
        "/api/ask-corpus/content-hash-duplicates",
        response_model=ContentHashDuplicatesResponse,
    )
    def content_hash_duplicates(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        duplicate_hash_rows = session.execute(
            select(AskCorpusDoc.content_hash, func.count())
            .where(AskCorpusDoc.content_hash.is_not(None))
            .group_by(AskCorpusDoc.content_hash)
            .having(func.count() > 1)
        ).all()

        duplicate_hashes = {row[0] for row in duplicate_hash_rows}
        rows = session.execute(
            select(AskCorpusDoc.server_id, AskCorpusDoc.content_hash)
        ).all()

        grouped_server_ids: dict[str, list[Any]] = defaultdict(list)
        grouped_doc_counts: dict[str, int] = defaultdict(int)

        for server_id, content_hash in rows:
            if content_hash not in duplicate_hashes:
                continue
            grouped_doc_counts[content_hash] += 1
            if server_id not in grouped_server_ids[content_hash]:
                grouped_server_ids[content_hash].append(server_id)

        duplicate_groups = [
            {
                "content_hash": content_hash,
                "server_ids": grouped_server_ids[content_hash],
                "doc_count": grouped_doc_counts[content_hash],
            }
            for content_hash in sorted(duplicate_hashes, key=str)
        ]
        server_ids = {
            server_id
            for server_id, _ in rows
            if server_id is not None
        }

        return {
            "duplicate_hashes": duplicate_groups,
            "total_duplicate_hashes": len(duplicate_groups),
            "docs_scanned": len(rows),
            "distinct_servers": len(server_ids),
        }

    return api


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    AskCorpusDoc.__table__.create(bind=engine)
    session = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    session.execute(
        AskCorpusDoc.__table__.insert(),
        [
            {
                "server_id": "server-a",
                "snippet": "first duplicate document",
                "terms": json.dumps(["first", "duplicate"]),
                "content_hash": "h1",
                "indexed_at": now,
            },
            {
                "server_id": "server-b",
                "snippet": "second duplicate document",
                "terms": json.dumps(["second", "duplicate"]),
                "content_hash": "h1",
                "indexed_at": now,
            },
            {
                "server_id": "server-c",
                "snippet": "unique document",
                "terms": json.dumps(["unique"]),
                "content_hash": "h2",
                "indexed_at": now,
            },
            {
                "server_id": "server-d",
                "snippet": "another unique document",
                "terms": json.dumps(["another"]),
                "content_hash": "h3",
                "indexed_at": now,
            },
        ],
    )
    session.commit()

    def override_get_session():
        yield session

    api = create_app()
    api.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(api) as client:
            response = client.get("/api/ask-corpus/content-hash-duplicates")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_duplicate_hashes"] == 1
            duplicate_group = payload["duplicate_hashes"][0]
            assert duplicate_group["content_hash"] == "h1"
            assert set(duplicate_group["server_ids"]) == {"server-a", "server-b"}
            assert len(duplicate_group["server_ids"]) == 2
            assert duplicate_group["doc_count"] == 2
            assert payload["docs_scanned"] == 4
            assert payload["distinct_servers"] == 4
    finally:
        api.dependency_overrides.clear()
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()