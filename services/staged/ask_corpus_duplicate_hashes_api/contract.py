from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


class DuplicateGroup(BaseModel):
    content_hash: str
    doc_count: int
    server_ids: List[str]
    server_names: List[str]
    first_indexed_at: datetime
    last_indexed_at: datetime


class DuplicateHashesResponse(BaseModel):
    generated_at: datetime
    total_duplicate_groups: int
    total_duplicate_docs: int
    total_wasted_docs: int
    groups: List[DuplicateGroup]


def build_service(session=Depends(get_session)) -> DuplicateHashesResponse:
    group_rows = (
        session.query(
            AskCorpusDoc.content_hash.label("content_hash"),
            func.count(AskCorpusDoc.content_hash).label("doc_count"),
            func.min(AskCorpusDoc.indexed_at).label("first_indexed_at"),
            func.max(AskCorpusDoc.indexed_at).label("last_indexed_at"),
        )
        .group_by(AskCorpusDoc.content_hash)
        .having(func.count(AskCorpusDoc.content_hash) > 1)
        .order_by(
            func.count(AskCorpusDoc.content_hash).desc(),
            AskCorpusDoc.content_hash.asc(),
        )
        .limit(100)
        .all()
    )

    hashes = [row.content_hash for row in group_rows]
    members_by_hash = {content_hash: [] for content_hash in hashes}

    if hashes:
        member_rows = (
            session.query(
                AskCorpusDoc.content_hash.label("content_hash"),
                AskCorpusDoc.server_id.label("server_id"),
                McpServerRegistry.name.label("server_name"),
            )
            .outerjoin(
                McpServerRegistry,
                AskCorpusDoc.server_id == McpServerRegistry.server_id,
            )
            .filter(AskCorpusDoc.content_hash.in_(hashes))
            .order_by(
                AskCorpusDoc.content_hash.asc(),
                AskCorpusDoc.server_id.asc(),
            )
            .all()
        )
        for row in member_rows:
            members_by_hash[row.content_hash].append((row.server_id, row.server_name))

    groups = []
    for row in group_rows:
        members = members_by_hash[row.content_hash]
        server_ids = []
        server_names = []
        seen_servers = set()

        for server_id, server_name in members:
            if server_id in seen_servers:
                continue
            seen_servers.add(server_id)
            server_ids.append(server_id)
            if server_name is not None:
                server_names.append(server_name)

        groups.append(
            DuplicateGroup(
                content_hash=row.content_hash,
                doc_count=row.doc_count,
                server_ids=server_ids,
                server_names=server_names,
                first_indexed_at=row.first_indexed_at,
                last_indexed_at=row.last_indexed_at,
            )
        )

    total_duplicate_docs = sum(group.doc_count for group in groups)

    return DuplicateHashesResponse(
        generated_at=datetime.now(timezone.utc),
        total_duplicate_groups=len(groups),
        total_duplicate_docs=total_duplicate_docs,
        total_wasted_docs=total_duplicate_docs - len(groups),
        groups=groups,
    )


router = APIRouter()


@router.get(
    "/api/ask-corpus/duplicates",
    response_model=DuplicateHashesResponse,
)
def get_duplicate_hashes(session=Depends(get_session)) -> DuplicateHashesResponse:
    return build_service(session=session)


def create_app() -> FastAPI:
    application = FastAPI()
    application.include_router(router)
    return application


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    terms TEXT,
                    content_hash TEXT NOT NULL,
                    indexed_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT NOT NULL,
                    name TEXT
                )
                """
            )
        )
        connection.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:id, :name)"),
            [
                {"id": "server-a", "name": "Server A"},
                {"id": "server-b", "name": "Server B"},
            ],
        )

    now = datetime.now(timezone.utc)
    rows = [
        {
            "server_id": "server-a",
            "snippet": "duplicate one",
            "terms": "one",
            "content_hash": "shared-hash",
            "indexed_at": now,
        },
        {
            "server_id": "server-b",
            "snippet": "duplicate two",
            "terms": "two",
            "content_hash": "shared-hash",
            "indexed_at": now,
        },
        {
            "server_id": "server-a",
            "snippet": "duplicate three",
            "terms": "three",
            "content_hash": "shared-hash",
            "indexed_at": now,
        },
        {
            "server_id": "server-b",
            "snippet": "unique one",
            "terms": "four",
            "content_hash": "unique-hash-one",
            "indexed_at": now,
        },
        {
            "server_id": "server-a",
            "snippet": "unique two",
            "terms": "five",
            "content_hash": "unique-hash-two",
            "indexed_at": now,
        },
    ]

    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        for row in rows:
            testing_session.execute(
                text(
                    """
                    INSERT INTO ask_corpus_index
                        (server_id, snippet, terms, content_hash, indexed_at)
                    VALUES
                        (:server_id, :snippet, :terms, :content_hash, :indexed_at)
                    """
                ),
                row,
            )
        testing_session.commit()

        def override_get_session():
            return testing_session

        that_app = FastAPI()
        that_app.include_router(router)
        that_app.dependency_overrides[get_session] = override_get_session

        with TestClient(that_app) as client:
            response = client.get("/api/ask-corpus/duplicates")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_duplicate_groups"] == 1
            assert payload["groups"][0]["doc_count"] == 3
            assert payload["total_duplicate_docs"] == 3
            assert payload["total_wasted_docs"] == 2
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()