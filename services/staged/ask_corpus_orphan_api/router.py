from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry

try:
    from .logic import get_ask_corpus_orphans
except ImportError:
    get_ask_corpus_orphans = None


class OrphanDocument(BaseModel):
    server_id: str
    content_hash: str
    indexed_at: datetime


class UnindexedServer(BaseModel):
    server_id: str
    name: str


class OrphanSummary(BaseModel):
    orphan_document_count: int
    orphan_server_count: int
    unindexed_server_count: int


class AskCorpusOrphansResponse(BaseModel):
    orphan_documents: List[OrphanDocument]
    unindexed_servers: List[UnindexedServer]
    summary: OrphanSummary


router = APIRouter(prefix="/api", tags=["ask-corpus"])


def _query_orphans(
    session: Session,
    limit: int,
    registry_source: Optional[str],
) -> Dict[str, Any]:
    orphan_query = (
        select(
            AskCorpusDoc.server_id,
            AskCorpusDoc.content_hash,
            AskCorpusDoc.indexed_at,
        )
        .outerjoin(
            McpServerRegistry,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
        .where(McpServerRegistry.server_id.is_(None))
        .order_by(AskCorpusDoc.indexed_at.desc())
        .limit(limit)
    )

    unindexed_query = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
        )
        .outerjoin(
            AskCorpusDoc,
            McpServerRegistry.server_id == AskCorpusDoc.server_id,
        )
        .where(AskCorpusDoc.server_id.is_(None))
        .order_by(McpServerRegistry.name.asc())
        .limit(limit)
    )

    if registry_source is not None:
        unindexed_query = unindexed_query.where(
            McpServerRegistry.registry_source == registry_source
        )

    orphan_documents = [
        {
            "server_id": row.server_id,
            "content_hash": row.content_hash,
            "indexed_at": row.indexed_at,
        }
        for row in session.execute(orphan_query).all()
    ]
    unindexed_servers = [
        {
            "server_id": row.server_id,
            "name": row.name,
        }
        for row in session.execute(unindexed_query).all()
    ]

    return {
        "orphan_documents": orphan_documents,
        "unindexed_servers": unindexed_servers,
        "summary": {
            "orphan_document_count": len(orphan_documents),
            "orphan_server_count": len(
                {item["server_id"] for item in orphan_documents}
            ),
            "unindexed_server_count": len(unindexed_servers),
        },
    }


@router.get(
    "/ask-corpus/orphans",
    response_model=AskCorpusOrphansResponse,
)
def ask_corpus_orphans(
    limit: int = Query(default=200),
    registry_source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    bounded_limit = max(1, min(1000, limit))
    if get_ask_corpus_orphans is not None:
        return get_ask_corpus_orphans(
            session,
            limit=bounded_limit,
            registry_source=registry_source,
        )
    return _query_orphans(session, bounded_limit, registry_source)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {AskCorpusDoc.__tablename__} (
                    server_id VARCHAR NOT NULL,
                    content_hash VARCHAR NOT NULL,
                    indexed_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE {McpServerRegistry.__tablename__} (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    registry_source VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {AskCorpusDoc.__tablename__}
                    (server_id, content_hash, indexed_at)
                VALUES
                    ('server-both', 'hash-both', '2025-01-02 03:04:05'),
                    ('server-orphan', 'hash-orphan', '2025-01-03 03:04:05')
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO {McpServerRegistry.__tablename__}
                    (server_id, name, registry_source)
                VALUES
                    ('server-both', 'Both Server', 'catalog'),
                    ('server-unindexed', 'Unindexed Server', 'catalog')
                """
            )
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/ask-corpus/orphans")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["orphan_document_count"] == 1
    assert payload["summary"]["unindexed_server_count"] == 1
    assert "server-orphan" in {
        item["server_id"] for item in payload["orphan_documents"]
    }
    assert "server-unindexed" in {
        item["server_id"] for item in payload["unindexed_servers"]
    }
    print("PASS")