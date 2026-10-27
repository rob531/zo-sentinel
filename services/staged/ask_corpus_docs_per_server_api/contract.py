from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


class ServerCorpusDocs(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    doc_count: int
    distinct_content_count: int
    first_indexed_at: Optional[str]
    last_indexed_at: Optional[str]


class CorpusDocsPerServerResponse(BaseModel):
    registry_server_count: int
    servers_with_docs: int
    coverage_ratio: float
    servers: list[ServerCorpusDocs]
    zero_doc_server_count: int
    generated_at: str


router = APIRouter()


def _as_iso8601(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


@router.get(
    "/ask/corpus-docs-per-server",
    response_model=CorpusDocsPerServerResponse,
)
def get_corpus_docs_per_server(
    limit: int = Query(default=50, ge=1, le=1000),
    min_docs: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> CorpusDocsPerServerResponse:
    corpus_counts = (
        select(
            AskCorpusDoc.server_id.label("server_id"),
            func.count().label("doc_count"),
            func.count(func.distinct(AskCorpusDoc.content_hash)).label(
                "distinct_content_count"
            ),
            func.min(AskCorpusDoc.indexed_at).label("first_indexed_at"),
            func.max(AskCorpusDoc.indexed_at).label("last_indexed_at"),
        )
        .group_by(AskCorpusDoc.server_id)
        .subquery()
    )

    registry_server_count = int(
        session.execute(
            select(func.count(func.distinct(McpServerRegistry.server_id)))
        ).scalar_one()
        or 0
    )

    servers_with_docs = int(
        session.execute(
            select(func.count(func.distinct(McpServerRegistry.server_id)))
            .select_from(McpServerRegistry)
            .join(
                AskCorpusDoc,
                AskCorpusDoc.server_id == McpServerRegistry.server_id,
            )
        ).scalar_one()
        or 0
    )

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            func.coalesce(corpus_counts.c.doc_count, 0).label("doc_count"),
            func.coalesce(corpus_counts.c.distinct_content_count, 0).label(
                "distinct_content_count"
            ),
            corpus_counts.c.first_indexed_at,
            corpus_counts.c.last_indexed_at,
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            corpus_counts,
            corpus_counts.c.server_id == McpServerRegistry.server_id,
        )
        .where(
            func.coalesce(corpus_counts.c.doc_count, 0) >= min_docs,
        )
        .order_by(
            func.coalesce(corpus_counts.c.doc_count, 0).desc(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(limit)
    )

    servers = [
        ServerCorpusDocs(
            server_id=row.server_id,
            name=row.name,
            risk_tier=row.risk_tier,
            doc_count=int(row.doc_count or 0),
            distinct_content_count=int(row.distinct_content_count or 0),
            first_indexed_at=_as_iso8601(row.first_indexed_at),
            last_indexed_at=_as_iso8601(row.last_indexed_at),
        )
        for row in session.execute(statement).all()
    ]

    coverage_ratio = (
        servers_with_docs / registry_server_count
        if registry_server_count
        else 0.0
    )

    return CorpusDocsPerServerResponse(
        registry_server_count=registry_server_count,
        servers_with_docs=servers_with_docs,
        coverage_ratio=coverage_ratio,
        servers=servers,
        zero_doc_server_count=max(registry_server_count - servers_with_docs, 0),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


app = FastAPI()
app.include_router(router, prefix="/api")


def _self_test() -> int:
    from datetime import datetime as datetime_type

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id VARCHAR NOT NULL,
                    snippet TEXT,
                    terms JSON,
                    content_hash VARCHAR,
                    indexed_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR NOT NULL,
                    name VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES (:server_id, :name, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "name": "Server A",
                    "risk_tier": "low",
                },
                {
                    "server_id": "server-b",
                    "name": "Server B",
                    "risk_tier": "medium",
                },
                {
                    "server_id": "server-c",
                    "name": "Server C",
                    "risk_tier": "high",
                },
            ],
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index (
                    server_id,
                    snippet,
                    terms,
                    content_hash,
                    indexed_at
                )
                VALUES (
                    :server_id,
                    :snippet,
                    :terms,
                    :content_hash,
                    :indexed_at
                )
                """
            ),
            [
                {
                    "server_id": "server-a",
                    "snippet": "first document",
                    "terms": "[]",
                    "content_hash": "hash-a-1",
                    "indexed_at": datetime_type(2024, 1, 1, 0, 0, 0),
                },
                {
                    "server_id": "server-a",
                    "snippet": "second document",
                    "terms": "[]",
                    "content_hash": "hash-a-2",
                    "indexed_at": datetime_type(2024, 1, 2, 0, 0, 0),
                },
                {
                    "server_id": "server-b",
                    "snippet": "third document",
                    "terms": "[]",
                    "content_hash": "hash-b-1",
                    "indexed_at": datetime_type(2024, 1, 3, 0, 0, 0),
                },
            ],
        )

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    local_app = FastAPI()
    local_app.include_router(router, prefix="/api")
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/ask/corpus-docs-per-server")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["registry_server_count"] == 3
        assert payload["servers_with_docs"] == 2
        assert payload["zero_doc_server_count"] == 1
        assert payload["servers"][0]["doc_count"] == 2
        assert payload["servers"][0]["server_id"] == "server-a"

    engine.dispose()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())