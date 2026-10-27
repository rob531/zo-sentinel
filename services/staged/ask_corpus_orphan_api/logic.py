from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


class OrphanDocument(BaseModel):
    server_id: str
    content_hash: str
    indexed_at: datetime


class UnindexedServer(BaseModel):
    server_id: str
    name: str | None = None


class OrphanSummary(BaseModel):
    orphan_document_count: int
    orphan_server_count: int
    unindexed_server_count: int


class AskCorpusOrphansResponse(BaseModel):
    orphan_documents: list[OrphanDocument]
    unindexed_servers: list[UnindexedServer]
    summary: OrphanSummary


router = APIRouter()


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, 1000))


@router.get(
    "/api/ask-corpus/orphans",
    response_model=AskCorpusOrphansResponse,
)
def list_ask_corpus_orphans(
    limit: int = Query(default=200),
    registry_source: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> AskCorpusOrphansResponse:
    limit = _clamp_limit(limit)

    orphan_documents_statement = (
        select(
            AskCorpusDoc.server_id,
            AskCorpusDoc.content_hash,
            AskCorpusDoc.indexed_at,
        )
        .select_from(AskCorpusDoc)
        .outerjoin(
            McpServerRegistry,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
        .where(McpServerRegistry.server_id.is_(None))
        .order_by(AskCorpusDoc.indexed_at.desc())
        .limit(limit)
    )

    unindexed_servers_statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            AskCorpusDoc,
            McpServerRegistry.server_id == AskCorpusDoc.server_id,
        )
        .where(AskCorpusDoc.server_id.is_(None))
        .order_by(McpServerRegistry.name.asc())
        .limit(limit)
    )

    if registry_source is not None:
        unindexed_servers_statement = unindexed_servers_statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    orphan_document_count = session.execute(
        select(func.count())
        .select_from(AskCorpusDoc)
        .outerjoin(
            McpServerRegistry,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
        .where(McpServerRegistry.server_id.is_(None))
    ).scalar_one()

    orphan_server_count = session.execute(
        select(func.count(func.distinct(AskCorpusDoc.server_id)))
        .select_from(AskCorpusDoc)
        .outerjoin(
            McpServerRegistry,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
        .where(McpServerRegistry.server_id.is_(None))
    ).scalar_one()

    unindexed_count_statement = (
        select(func.count(McpServerRegistry.server_id))
        .select_from(McpServerRegistry)
        .outerjoin(
            AskCorpusDoc,
            McpServerRegistry.server_id == AskCorpusDoc.server_id,
        )
        .where(AskCorpusDoc.server_id.is_(None))
    )
    if registry_source is not None:
        unindexed_count_statement = unindexed_count_statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    unindexed_server_count = session.execute(
        unindexed_count_statement
    ).scalar_one()

    orphan_documents = [
        OrphanDocument(
            server_id=row.server_id,
            content_hash=row.content_hash,
            indexed_at=row.indexed_at,
        )
        for row in session.execute(orphan_documents_statement).all()
    ]

    unindexed_servers = [
        UnindexedServer(
            server_id=row.server_id,
            name=row.name,
        )
        for row in session.execute(unindexed_servers_statement).all()
    ]

    return AskCorpusOrphansResponse(
        orphan_documents=orphan_documents,
        unindexed_servers=unindexed_servers,
        summary=OrphanSummary(
            orphan_document_count=orphan_document_count,
            orphan_server_count=orphan_server_count,
            unindexed_server_count=unindexed_server_count,
        ),
    )


if __name__ == "__main__":
    from datetime import timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id TEXT PRIMARY KEY,
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
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, registry_source)
                VALUES (:shared_id, :shared_name, :registry_source),
                       (:unindexed_id, :unindexed_name, :registry_source)
                """
            ),
            {
                "shared_id": "shared-server",
                "shared_name": "Shared Server",
                "unindexed_id": "unindexed-server",
                "unindexed_name": "Unindexed Server",
                "registry_source": "test",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, content_hash, indexed_at)
                VALUES (:shared_id, :shared_hash, :shared_time),
                       (:orphan_id, :orphan_hash, :orphan_time)
                """
            ),
            {
                "shared_id": "shared-server",
                "shared_hash": "shared-hash",
                "shared_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "orphan_id": "orphan-server",
                "orphan_hash": "orphan-hash",
                "orphan_time": datetime(2024, 1, 2, tzinfo=timezone.utc),
            },
        )

    def override_get_session():
        with test_engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield connection
            finally:
                transaction.rollback()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/ask-corpus/orphans")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["summary"]["orphan_document_count"] == 1
        assert payload["summary"]["unindexed_server_count"] == 1
        assert (
            "orphan-server"
            in {item["server_id"] for item in payload["orphan_documents"]}
        )
        assert (
            "unindexed-server"
            in {item["server_id"] for item in payload["unindexed_servers"]}
        )

    print("PASS")